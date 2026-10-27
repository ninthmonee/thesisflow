"""Durable, scoped research use cases without in-process task execution."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select

from tradingagents.agents.rating import run_rating
from tradingagents.web.api.schemas import CreateResearchRequest
from tradingagents.web.domain.research_dates import current_research_date
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import Database
from tradingagents.web.infrastructure.models import (
    Instrument,
    Membership,
    Report,
    ResearchRun,
    RunAttempt,
    RunEvent,
    RunManifest,
)
from tradingagents.web.infrastructure.runtime_contract import (
    current_graph_contract,
    verify_graph_contract,
)

from .research_repository import TERMINAL, TaskRepository

REPORT_LABELS = {
    "market_report": "技术分析报告",
    "sentiment_report": "社交情绪报告",
    "news_report": "新闻分析报告",
    "fundamentals_report": "基本面报告",
    "bull_history": "多头研究",
    "bear_history": "空头研究",
    "investment_plan": "研究经理决策",
    "trader_investment_plan": "交易计划",
    "aggressive_history": "激进风险观点",
    "conservative_history": "保守风险观点",
    "neutral_history": "中立风险观点",
    "final_trade_decision": "组合经理决策",
    "memory_note": "历史经验状态",
}
SAFE_RUNTIME_KEYS = frozenset(
    {
        "llm_provider",
        "quick_think_provider",
        "deep_think_provider",
        "quick_think_llm",
        "deep_think_llm",
        "output_language",
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "max_tool_rounds",
        "max_recur_limit",
        "temperature",
        "llm_max_retries",
        "max_tokens",
        "google_thinking_level",
        "openai_reasoning_effort",
        "anthropic_effort",
        "data_vendors",
        "tool_vendors",
        "news_article_limit",
        "global_news_article_limit",
        "global_news_lookback_days",
        "global_news_queries",
        "benchmark_ticker",
        "holding_period_days",
        "benchmark_map",
    }
)
SECRET_KEYS = {
    "api_key",
    "secret",
    "password",
    "authorization",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
}
# These are the market module's explicit ETF proxies. Other discovered
# instruments use their catalog type; never route known funds through company tools.
MARKET_ETF_PROXIES = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "XLK",
        "XLF",
        "XLV",
        "XLY",
        "XLP",
        "XLE",
        "XLI",
        "XLB",
        "XLU",
        "XLRE",
        "XLC",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validate_public_manifest(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in SECRET_KEYS
                or normalized.endswith("_api_key")
                or normalized.startswith("__")
            ):
                raise ResearchError(
                    "secret_in_manifest", "研究清单只能包含凭据引用，不能保存凭据", 422
                )
            validate_public_manifest(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_public_manifest(item)


class ResearchService:
    def __init__(
        self,
        database: Database,
        artifacts_root: str | Path,
        config_snapshot_provider: Callable[[], Mapping[str, Any]] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        data_snapshot_provider: Callable[[str, str], Mapping[str, Any]] | None = None,
    ):
        self.database = database
        self.artifacts_root = Path(artifacts_root).expanduser().resolve()
        self.config_snapshot_provider = config_snapshot_provider
        self.data_snapshot_provider = data_snapshot_provider
        self.tasks = TaskRepository(database, clock)

    @staticmethod
    def _scope(session, workspace_id: str, owner_id: str, *, write: bool = False) -> None:
        member = session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == owner_id,
            )
        )
        if member is None:
            raise ResearchError("workspace_not_found", "工作区不存在", 404)
        if write and member.role not in {"owner", "researcher"}:
            raise ResearchError("research_forbidden", "当前权限不能发起或修改研究", 403)

    def run_directory(self, workspace_id: str, run_id: str) -> Path:
        # Database IDs never become an unchecked path component.
        workspace_part = hashlib.sha256(workspace_id.encode()).hexdigest()[:24]
        from uuid import UUID

        safe_run = str(UUID(run_id))
        return self.artifacts_root / workspace_part / safe_run

    def create_run(
        self,
        request: Mapping[str, Any] | CreateResearchRequest,
        *,
        workspace_id: str,
        owner_id: str,
        idempotency_key: str | None = None,
        manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = CreateResearchRequest.model_validate(
            request.model_dump() if isinstance(request, CreateResearchRequest) else dict(request)
        ).model_dump()
        if body["date"] > current_research_date():
            raise ResearchError("future_research_date", "分析日期不能在未来", 422)
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id, write=True)
            catalog_types = set(
                session.scalars(
                    select(Instrument.asset_type).where(Instrument.symbol == body["ticker"])
                )
            )
        if (
            body["ticker"].startswith("^")
            or body["ticker"] in MARKET_ETF_PROXIES
            or catalog_types.intersection({"etf", "index", "fund"})
        ):
            raise ResearchError(
                "research_asset_not_supported",
                "当前 TradingAgents 个股研究面向公司股票；ETF和指数请使用行情与市场分析。",
                422,
            )
        crypto = body["ticker"].endswith(("-USD", "-USDT", "-USDC", "-BTC", "-ETH"))
        if crypto:
            body["analysts"] = [item for item in body["analysts"] if item != "fundamentals"]
            if not body["analysts"]:
                raise ResearchError("analysts_required", "加密资产需要技术、新闻或情绪分析师", 422)
        body["checkpoint"] = body["checkpoint"] if body["checkpoint"] is not None else True
        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > 160
        ):
            raise ResearchError("invalid_idempotency_key", "幂等键长度须为 1 至 160", 422)
        selected = deepcopy(dict(manifest or {}))
        context_ids = body.get("context_ids", [])
        context_inputs = selected.get("context_inputs", [])
        if (
            not isinstance(context_inputs, list)
            or len(context_inputs) != len(context_ids)
            or any(
                not isinstance(item, dict)
                or item.get("id") != identifier
                or not isinstance(item.get("hash"), str)
                or len(item["hash"]) != 64
                or any(character not in "0123456789abcdef" for character in item["hash"])
                or not isinstance(item.get("data"), dict)
                for identifier, item in zip(context_ids, context_inputs, strict=False)
            )
        ):
            raise ResearchError(
                "research_context_unresolved",
                "附加资料与所选引用未按顺序完整冻结，请重新选择资料",
                422,
            )
        runtime = self.config_snapshot_provider() if self.config_snapshot_provider else {}
        public_runtime = {
            key: deepcopy(value) for key, value in runtime.items() if key in SAFE_RUNTIME_KEYS
        }
        if self.data_snapshot_provider is not None:
            data_connections = deepcopy(dict(self.data_snapshot_provider(workspace_id, owner_id)))
        else:
            from tradingagents.web.application.research_data_connections import (
                ResearchDataConnectionService,
            )
            from tradingagents.web.infrastructure.seeds import LocalWorkspace

            data_connections = ResearchDataConnectionService(
                self.database,
                LocalWorkspace(
                    user_id=owner_id, workspace_id=workspace_id, watchlist_id="", name="研究工作区"
                ),
            ).freeze_for_run(workspace_id, owner_id)
        document = {
            "schema_version": "1",
            "graph_contract_version": current_graph_contract(),
            "workspace_id": workspace_id,
            "owner_id": owner_id,
            "request": body,
            "asset_type": "crypto" if crypto else "stock",
            "runtime": public_runtime,
            "selection": selected,
            "data_connections": data_connections,
            "memory_scope": "run",
        }
        model_snapshot = selected.get("model_snapshot")
        if isinstance(model_snapshot, Mapping):
            roles = model_snapshot.get("roles", {})
            for tier in ("quick", "deep"):
                model = roles.get(tier, {})
                if model.get("provider") and model.get("model"):
                    public_runtime[f"{tier}_think_provider"] = model["provider"]
                    public_runtime[f"{tier}_think_llm"] = model["model"]
                    if tier == "quick":
                        public_runtime["llm_provider"] = model["provider"]
        validate_public_manifest(document)
        try:
            request_digest = content_hash(
                {"request": body, "selection": selected, "data_connections": data_connections}
            )
            manifest_digest = content_hash(document)
        except (TypeError, ValueError):
            raise ResearchError(
                "invalid_manifest", "研究配置须为可序列化的有限数值和配置", 422
            ) from None

        with self.tasks.transaction() as session:
            self._scope(session, workspace_id, owner_id, write=True)
            if idempotency_key is not None:
                previous = session.scalar(
                    select(ResearchRun).where(
                        ResearchRun.workspace_id == workspace_id,
                        ResearchRun.owner_id == owner_id,
                        ResearchRun.idempotency_key == idempotency_key,
                    )
                )
                if previous is not None:
                    if previous.request_hash != request_digest:
                        raise ResearchError("idempotency_conflict", "相同幂等键对应不同研究输入")
                    run_id = previous.id
                else:
                    run_id = None
            else:
                run_id = None
            if run_id is None:
                run = ResearchRun(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    owner_id=owner_id,
                    ticker=body["ticker"],
                    analysis_date=date.fromisoformat(body["date"]),
                    status="queued",
                    request_hash=request_digest,
                    idempotency_key=idempotency_key,
                    created_at=self.tasks.now(),
                )
                session.add(run)
                session.flush()
                session.add(
                    RunManifest(
                        run_id=run.id,
                        workspace_id=workspace_id,
                        schema_version="1",
                        content=document,
                        content_hash=manifest_digest,
                    )
                )
                self.tasks.append_event(
                    session, run, "state", {"status": "queued", "stage": run.stage}
                )
                run_id = run.id
        return self.get_run(run_id, workspace_id=workspace_id, owner_id=owner_id)

    def get_manifest(self, run_id: str, *, workspace_id: str, owner_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            manifest = session.get(RunManifest, run_id)
            if manifest is None:
                raise ResearchError("manifest_missing", "研究配置清单缺失", 404)
            return deepcopy(manifest.content)

    def _snapshot(self, session, run: ResearchRun, *, detail: bool = True) -> dict[str, Any]:
        manifest = session.get(RunManifest, run.id)
        document = manifest.content if manifest else {}
        request = document.get("request", {})
        reports = {}
        exports = set()
        stored_rating = None
        decision_text = None
        for report in session.scalars(
            select(Report).where(Report.run_id == run.id).order_by(Report.version)
        ):
            if report.kind.startswith("_export_"):
                exports.add(report.kind)
            elif report.kind == "_rating":
                stored_rating = report.content
            elif report.kind == "_research_quality":
                continue
            else:
                if report.kind == "final_trade_decision":
                    decision_text = report.content
                if detail and report.content is not None:
                    reports[report.kind] = report.content
        stats = self._usage_summary(session, run)
        events = []
        if detail:
            rows = session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run.id, RunEvent.event_type != "stats")
                .order_by(RunEvent.event_seq.desc())
                .limit(400)
            ).all()
            for event in reversed(rows):
                payload = event.payload
                message = payload.get("label") or payload.get("message")
                if event.event_type == "report":
                    message = REPORT_LABELS.get(payload.get("section"), "研究报告") + "已更新"
                if event.event_type == "state":
                    message = {
                        "queued": "研究已排队",
                        "running": "研究正在执行",
                        "cancelling": "正在取消",
                        "cancelled": "研究已取消",
                        "completed": "研究已完成",
                        "failed": "研究执行失败",
                        "interrupted": "执行已中断",
                    }.get(payload.get("status"), "状态已更新")
                events.append(
                    {
                        "id": event.event_seq,
                        "time": event.occurred_at.isoformat(),
                        "type": event.event_type,
                        "message": message or "研究状态已更新",
                    }
                )
        from .research_evidence import ResearchEvidenceStore

        quality = ResearchEvidenceStore(self.database, self.artifacts_root).get_quality(
            run.id, workspace_id=run.workspace_id, owner_id=run.owner_id
        )
        raw_rating = stored_rating or (
            run_rating({"final_trade_decision": decision_text}) if decision_text else None
        )
        rating = raw_rating if run.status == "completed" and quality.eligible_for_rating else None
        return {
            "id": run.id,
            "ticker": run.ticker,
            "date": run.analysis_date.isoformat(),
            "analysts": request.get("analysts", []),
            "asset_type": document.get("asset_type", "stock"),
            "depth": request.get("depth", 1),
            "language": request.get("language", "Chinese"),
            "checkpoint": bool(request.get("checkpoint", False)),
            "status": run.status,
            "stage": run.stage,
            "revision": run.revision,
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "rating": rating,
            "raw_rating": raw_rating,
            "quality": quality.model_dump(mode="json"),
            "objective": request.get("objective"),
            "horizon": request.get("horizon"),
            "holding_state": request.get("holding_state", "unknown"),
            "max_llm_calls": request.get("max_llm_calls"),
            "max_tokens_total": request.get("max_tokens_total"),
            "max_runtime_seconds": request.get("max_runtime_seconds"),
            "reports": reports,
            "events": events,
            "error": run.error_message,
            "stats": stats,
            "report_url": f"/api/v1/runs/{run.id}/report" if "_export_html" in exports else None,
            "markdown_url": f"/api/v1/runs/{run.id}/report.md"
            if "_export_markdown" in exports
            else None,
        }

    @staticmethod
    def _usage_counter(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    def _usage_summary(self, session, run, *, exclude_attempt_id=None):
        attempts = session.scalars(
            select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.attempt_no)
        ).all()
        attempts = [attempt for attempt in attempts if attempt.id != exclude_attempt_id]
        latest = {}
        for event in session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.event_type == "stats")
            .order_by(RunEvent.event_seq.desc())
        ):
            if exclude_attempt_id is None or event.attempt_id != exclude_attempt_id:
                latest.setdefault(event.attempt_id, event.payload)
        observed = []
        segments = [(attempt.id, attempt) for attempt in attempts]
        if None in latest:
            segments.insert(0, (None, None))
        for identifier, attempt in segments:
            payload = latest.get(identifier, {})
            payload = payload if isinstance(payload, Mapping) else {}
            item = {
                key: self._usage_counter(payload.get(key))
                for key in ("llm_calls", "tool_calls", "tokens_in", "tokens_out")
            }
            elapsed = payload.get("elapsed_seconds", 0)
            elapsed = (
                float(elapsed)
                if isinstance(elapsed, (int, float))
                and not isinstance(elapsed, bool)
                and math.isfinite(elapsed)
                and elapsed >= 0
                else 0.0
            )
            if attempt is not None:
                end = attempt.finished_at or (
                    self.tasks.now() if attempt.status == "running" else None
                )
                if end is not None:
                    elapsed = max(elapsed, (end - attempt.created_at).total_seconds())
            reported = payload.get("usage_reported_calls")
            reported = (
                reported
                if isinstance(reported, int)
                and not isinstance(reported, bool)
                and 0 <= reported <= item["llm_calls"]
                else None
            )
            uncertain = max(
                self._usage_counter(payload.get("uncertain_calls")),
                item["llm_calls"] - (reported or 0),
            )
            quality = payload.get("usage_quality")
            if quality not in {"complete", "partial", "unknown"}:
                quality = "partial" if item["tokens_in"] or item["tokens_out"] else "unknown"
            if reported is None or uncertain:
                quality = (
                    "partial" if reported or item["tokens_in"] or item["tokens_out"] else "unknown"
                )
            item.update(
                attempt_id=identifier,
                attempt_no=attempt.attempt_no if attempt else None,
                status=attempt.status if attempt else "legacy",
                elapsed_seconds=max(0, round(elapsed, 1)),
                usage_reported_calls=reported,
                uncertain_calls=uncertain,
                usage_quality=quality,
            )
            observed.append(item)
        total = {
            key: sum(item[key] for item in observed)
            for key in (
                "llm_calls",
                "tool_calls",
                "tokens_in",
                "tokens_out",
                "elapsed_seconds",
                "uncertain_calls",
            )
        }
        total["elapsed_seconds"] = round(total["elapsed_seconds"], 1)
        total["usage_reported_calls"] = (
            sum(item["usage_reported_calls"] for item in observed)
            if all(item["usage_reported_calls"] is not None for item in observed)
            else None
        )
        total["usage_quality"] = (
            "complete"
            if all(item["usage_quality"] == "complete" for item in observed)
            else "partial"
            if any(
                item["usage_quality"] != "unknown" or item["tokens_in"] or item["tokens_out"]
                for item in observed
            )
            else "unknown"
        )
        if not observed and run.status not in {"queued", "pending"}:
            total["usage_quality"], total["usage_reported_calls"] = "unknown", None
        total["attempt_count"] = len(attempts)
        total["current_attempt"] = observed[-1] if attempts else None
        return total

    def usage_totals(
        self,
        run_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        exclude_attempt_id: str | None = None,
    ):
        """Known cumulative usage, with each attempt's latest counters included once."""
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            run = self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            if exclude_attempt_id is not None:
                attempt = session.get(RunAttempt, exclude_attempt_id)
                if attempt is None or attempt.run_id != run.id:
                    raise ResearchError("attempt_not_found", "执行尝试不存在", 404)
            return self._usage_summary(session, run, exclude_attempt_id=exclude_attempt_id)

    def get_run(self, run_id: str, *, workspace_id: str, owner_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            run = self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            return self._snapshot(session, run)

    def list_runs(
        self,
        *,
        workspace_id: str,
        owner_id: str,
        limit: int = 20,
        cursor: str | None = None,
        status: str | None = None,
        ticker: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ResearchError("invalid_page_size", "分页数量须为 1 至 100", 422)
        query = select(ResearchRun).where(
            ResearchRun.workspace_id == workspace_id, ResearchRun.owner_id == owner_id
        )
        if status:
            query = query.where(ResearchRun.status == status)
        if ticker:
            query = query.where(ResearchRun.ticker == ticker.strip().upper())
        if cursor:
            try:
                stamp, run_id = json.loads(
                    base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                )
                cutoff = datetime.fromisoformat(stamp)
                if cutoff.tzinfo is None:
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise ResearchError("invalid_cursor", "分页游标无效", 422) from None
            query = query.where(
                or_(
                    ResearchRun.created_at < cutoff,
                    and_(ResearchRun.created_at == cutoff, ResearchRun.id < run_id),
                )
            )
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            rows = session.scalars(
                query.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc()).limit(
                    limit + 1
                )
            ).all()
            items = [self._snapshot(session, row, detail=False) for row in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                encoded = canonical_json([last.created_at.isoformat(), last.id]).encode()
                next_cursor = base64.urlsafe_b64encode(encoded).decode().rstrip("=")
            return {"items": items, "next_cursor": next_cursor}

    def list_events(
        self, run_id: str, *, workspace_id: str, owner_id: str, after_seq: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        if after_seq < 0 or not 1 <= limit <= 1000:
            raise ResearchError("invalid_event_cursor", "事件游标或数量无效", 422)
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            rows = session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.event_seq > after_seq)
                .order_by(RunEvent.event_seq)
                .limit(limit)
            ).all()
            return [
                {
                    "event_id": f"{row.run_id}:{row.event_seq}",
                    "seq": row.event_seq,
                    "run_id": row.run_id,
                    "attempt_id": row.attempt_id,
                    "timestamp": row.occurred_at.isoformat(),
                    "schema_version": row.schema_version,
                    "type": row.event_type,
                    "payload": deepcopy(row.payload),
                }
                for row in rows
            ]

    def cancel_run(self, run_id: str, *, workspace_id: str, owner_id: str) -> dict[str, Any]:
        with self.tasks.transaction() as session:
            self._scope(session, workspace_id, owner_id, write=True)
            run = self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            if run.status == "queued":
                run.status, run.finished_at = "cancelled", self.tasks.now()
                self.tasks.append_event(
                    session, run, "state", {"status": run.status, "stage": run.stage}
                )
            elif run.status == "running":
                run.status = "cancelling"
                attempt = self.tasks.latest_attempt(session, run_id)
                self.tasks.append_event(
                    session,
                    run,
                    "state",
                    {"status": run.status, "stage": run.stage},
                    attempt.id if attempt else None,
                )
            elif run.status not in TERMINAL | {"cancelling"}:
                raise ResearchError("invalid_cancel_state", "当前状态无法取消")
        return self.get_run(run_id, workspace_id=workspace_id, owner_id=owner_id)

    def resume_run(self, run_id: str, *, workspace_id: str, owner_id: str) -> dict[str, Any]:
        from tradingagents.web.workers.checkpoints import CheckpointFiles

        self.tasks.reap_expired()
        with self.tasks.transaction() as session:
            self._scope(session, workspace_id, owner_id, write=True)
            run = self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            if run.status not in {"interrupted", "failed", "cancelled"}:
                raise ResearchError("invalid_resume_state", "只能继续已中断、失败或取消的研究")
            attempt = self.tasks.latest_attempt(session, run_id)
            manifest = session.get(RunManifest, run_id)
            if attempt is None or not attempt.checkpoint_ref or manifest is None:
                raise ResearchError("checkpoint_unavailable", "没有兼容检查点，请重新研究")
            verify_graph_contract(manifest.content)
            CheckpointFiles(self).validate_ref(
                attempt.checkpoint_ref, workspace_id, run_id, manifest.content_hash
            )
            run.status, run.finished_at = "queued", None
            run.error_code, run.error_message = None, None
            self.tasks.append_event(
                session, run, "state", {"status": run.status, "stage": run.stage}
            )
        return self.get_run(run_id, workspace_id=workspace_id, owner_id=owner_id)

    def report_file(
        self, run_id: str, format: str = "html", *, workspace_id: str, owner_id: str
    ) -> Path:
        if format not in {"html", "markdown", "md"}:
            raise ResearchError("invalid_report_format", "报告格式无效", 422)
        kind = "_export_html" if format == "html" else "_export_markdown"
        with self.database.session() as session:
            self._scope(session, workspace_id, owner_id)
            self.tasks.scoped_run(session, run_id, workspace_id, owner_id)
            report = session.scalar(
                select(Report)
                .where(Report.run_id == run_id, Report.kind == kind)
                .order_by(Report.version.desc())
                .limit(1)
            )
            if report is None or not report.file_path:
                raise ResearchError("report_not_found", "报告尚未生成", 404)
            path = (self.artifacts_root / report.file_path).resolve()
            expected = self.run_directory(workspace_id, run_id)
            if not path.is_relative_to(expected) or not path.is_file() or path.is_symlink():
                raise ResearchError("report_not_found", "报告不可用", 404)
            return path
