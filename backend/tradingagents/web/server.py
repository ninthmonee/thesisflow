"""Loopback-only web API, backed by the existing TradingAgents graph.

Research submissions accept run inputs rather than private Python configuration.
Dedicated connection settings accept write-only credentials. A worker owns a
fresh graph for each run; public snapshots contain only safe metadata and references.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager, nullcontext, suppress
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, ConfigDict, Field, field_validator

import tradingagents
from tradingagents.agents.rating import run_rating
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.date_window import date_timezone_scope
from tradingagents.dataflows.symbols import normalize_symbol, safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
from tradingagents.web.domain.research_dates import current_research_date

_ANALYSTS = ("market", "social", "news", "fundamentals")
_TERMINAL = {"completed", "failed", "interrupted"}
_HOSTS = {"localhost", "127.0.0.1"}
_MAX_BODY = 8192
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")
_REPORT_LABELS = {
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
_STAGES = {
    "initializing": 0,
    "analysts": 1,
    "research": 2,
    "trading": 3,
    "risk": 4,
    "portfolio": 5,
    "completed": 6,
}
_STAGE_LABELS = {
    "initializing": "正在初始化模型与标的信息",
    "analysts": "分析师正在并行研究",
    "research": "研究团队正在进行多空辩论",
    "trading": "正在制定交易计划",
    "risk": "风险团队正在评估交易建议",
    "portfolio": "组合经理正在形成最终决策",
    "completed": "分析完成",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _valid_id(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(404, "任务不存在") from None
    return value


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ticker: str = Field(min_length=1, max_length=32)
    date: str = Field(min_length=10, max_length=10)
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: list(_ANALYSTS), min_length=1, max_length=4
    )
    depth: Literal[1, 2, 3] = 1
    language: Literal["Chinese", "English"] = "Chinese"
    checkpoint: bool | None = None

    @field_validator("depth", mode="before")
    @classmethod
    def validate_depth(cls, value):
        if type(value) is not int:
            raise ValueError("研究深度须为 1、2 或 3")
        return value

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, value: str) -> str:
        value = normalize_symbol(value)
        safe_ticker_component(value)
        if not re.fullmatch(r"[A-Z0-9^][A-Z0-9._^=\-]*", value):
            raise ValueError("请输入有效证券代码")
        return value

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError("日期格式须为 YYYY-MM-DD") from None
        if value > current_research_date():
            raise ValueError("分析日期不能晚于今天")
        if value < "1970-01-01":
            raise ValueError("分析日期不能早于 1970-01-01")
        return value

    @field_validator("analysts")
    @classmethod
    def validate_analysts(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("分析师不可重复")
        return [key for key in _ANALYSTS if key in value]


def _provider_ready(provider: str) -> bool:
    if provider == "chatgpt":
        from tradingagents.llm_clients.chatgpt_auth import signed_in_as

        return bool(signed_in_as())
    if provider in {"ollama", "openai_compatible"}:
        return True
    if provider == "bedrock":
        # boto3 also supports shared credential files, SSO and IAM roles. Their
        # availability cannot be inferred without doing a network/auth request.
        return True
    env_name = PROVIDER_API_KEY_ENV.get(provider)
    return bool(env_name and os.getenv(env_name))


def public_config(config: dict, active_id: str | None) -> dict:
    provider = str(config.get("llm_provider") or "openai")
    quick_provider = str(config.get("quick_think_provider") or provider)
    deep_provider = str(config.get("deep_think_provider") or provider)
    ready = _provider_ready(quick_provider) and _provider_ready(deep_provider)
    services = [
        {
            "id": "model",
            "name": "模型服务",
            "configured": ready,
            "status": "configured" if ready else "missing",
        },
        {"id": "yfinance", "name": "Yahoo Finance", "configured": True, "status": "keyless"},
        {"id": "sec_edgar", "name": "SEC EDGAR", "configured": True, "status": "keyless"},
    ]
    for key, name, env in (
        ("fred", "FRED 宏观数据", "FRED_API_KEY"),
        ("alpha_vantage", "Alpha Vantage", "ALPHA_VANTAGE_API_KEY"),
        ("jev", "TypeSafe Jev", "TYPESAFE_API_KEY"),
    ):
        configured = bool(os.getenv(env))
        services.append(
            {
                "id": key,
                "name": name,
                "configured": configured,
                "status": "configured" if configured else "optional",
            }
        )
    return {
        "version": tradingagents.__version__,
        "today": current_research_date(),
        "provider": provider,
        "quick_provider": quick_provider,
        "deep_provider": deep_provider,
        "quick_model": config.get("quick_think_llm"),
        "deep_model": config.get("deep_think_llm"),
        "output_language": config.get("output_language", "Chinese"),
        "model_ready": ready,
        "services": services,
        "active_run_id": active_id,
    }


def _safe_error(exc: Exception) -> str:
    """Do not surface provider exceptions: they can contain headers, URLs and paths."""
    from tradingagents.web.api.research_data_schemas import ResearchDataConnectionError

    if isinstance(exc, ResearchDataConnectionError):
        return exc.message
    name = type(exc).__name__.lower()
    if "auth" in name or "permission" in name:
        return "模型授权失败，请在设置中心检查模型连接或本机 ChatGPT 授权。"
    if "ratelimit" in name:
        return "模型服务达到请求或订阅额度限制，请稍后重试。"
    if "timeout" in name or "connection" in name or "unreachable" in name:
        return "外部服务暂时无法连接，请检查网络后重试。"
    return "分析未能完成，请检查模型授权、网络及数据服务后重试。已生成的报告段落仍可查看。"


class RunStore:
    """Thread-safe snapshots, atomically persisted below an application-owned root."""

    def __init__(self, config: dict, graph_factory=None):
        self.config = deepcopy(config)
        self.root = Path(config["results_dir"]) / "web"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self.active_id: str | None = None
        self.graph_factory = graph_factory
        self.data_connection_freezer = None
        self.data_connection_context_provider = None
        self.data_connection_validator = None
        self.threads: dict[str, threading.Thread] = {}
        for entry in self.root.glob("*/run.json"):
            try:
                if entry.parent.is_symlink():
                    continue
                saved = json.loads(entry.read_text(encoding="utf-8"))
                run_id = _valid_id(entry.parent.name)
                if saved.get("id") != run_id:
                    continue
                if saved.get("status") in {"pending", "running"}:
                    saved.update(
                        status="interrupted",
                        stage="interrupted",
                        finished_at=_now(),
                        error="服务曾重启，此任务已中断。可重新提交相同输入，启用断点后会尝试恢复。",
                    )
                    saved["revision"] = saved.get("revision", 0) + 1
                self.runs[run_id] = saved
                self._persist(saved)
            except (OSError, ValueError, TypeError, HTTPException):
                continue

    def _persist(self, run: dict) -> None:
        directory = self.root / run["id"]
        directory.mkdir(exist_ok=True)
        temp = directory / "run.json.tmp"
        temp.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        temp.replace(directory / "run.json")

    def snapshot(self, run_id: str) -> dict:
        run_id = _valid_id(run_id)
        with self.lock:
            if run_id not in self.runs:
                raise HTTPException(404, "任务不存在")
            result = deepcopy(self.runs[run_id])
        if result["status"] == "running" and result.get("started_at"):
            result["stats"]["elapsed_seconds"] = round(
                (datetime.now(UTC) - datetime.fromisoformat(result["started_at"])).total_seconds(),
                1,
            )
        return result

    def history(self) -> list[dict]:
        with self.lock:
            ids = sorted(self.runs, key=lambda key: self.runs[key]["created_at"], reverse=True)
            return [self.snapshot(run_id) for run_id in ids]

    def update(self, run_id: str, *, event: tuple[str, str] | None = None, **fields) -> None:
        with self.lock:
            run = self.runs[run_id]
            run.update(fields)
            run["revision"] += 1
            if event:
                run["events"].append(
                    {"id": run["revision"], "time": _now(), "type": event[0], "message": event[1]}
                )
                run["events"] = run["events"][-400:]
            self._persist(run)

    def stage(self, run_id: str, stage: str) -> None:
        with self.lock:
            current = self.runs[run_id]["stage"]
            if _STAGES.get(stage, -1) > _STAGES.get(current, -1):
                self.update(run_id, stage=stage, event=("stage", _STAGE_LABELS[stage]))

    def create(self, request: RunRequest) -> dict:
        # The crypto graph has no corporate fundamentals analyst, matching the CLI.
        asset_type = "crypto" if request.ticker.endswith(_CRYPTO_SUFFIXES) else "stock"
        analysts = [
            a for a in request.analysts if not (asset_type == "crypto" and a == "fundamentals")
        ]
        if not analysts:
            raise HTTPException(422, "加密资产请至少选择技术、新闻或情绪分析师")
        with self.lock:
            if self.active_id is not None:
                raise HTTPException(
                    409, {"message": "已有分析正在运行，请等待完成", "run_id": self.active_id}
                )
            if not public_config(self.config, None)["model_ready"]:
                raise HTTPException(503, "模型尚未配置，请在设置中心配置模型或完成本机 ChatGPT 授权")
            run_id = str(uuid.uuid4())
            self.runs[run_id] = {
                "id": run_id,
                "ticker": request.ticker,
                "date": request.date,
                "analysts": analysts,
                "asset_type": asset_type,
                "depth": request.depth,
                "language": request.language,
                "checkpoint": request.checkpoint
                if request.checkpoint is not None
                else bool(self.config.get("checkpoint_enabled")),
                "status": "pending",
                "stage": "initializing",
                "revision": 0,
                "data_connections": self.data_connection_freezer()
                if self.data_connection_freezer is not None
                else None,
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "rating": None,
                "reports": {},
                "events": [],
                "error": None,
                "stats": {
                    "llm_calls": 0,
                    "tool_calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "elapsed_seconds": 0,
                },
                "report_url": None,
                "markdown_url": None,
            }
            self.active_id = run_id
            self.update(run_id, event=("system", "分析任务已创建"))
            initial = self.snapshot(run_id)
            thread = threading.Thread(
                target=self._execute,
                args=(run_id,),
                daemon=True,
                name=f"tradingagents-web-{run_id[:8]}",
            )
            self.threads[run_id] = thread
            try:
                thread.start()
            except RuntimeError:
                self.active_id = None
                self.update(
                    run_id, status="failed", error="无法启动后台分析任务", finished_at=_now()
                )
                raise HTTPException(503, "无法启动后台分析任务") from None
            return initial

    def _execute(self, run_id: str) -> None:
        started = time.monotonic()
        graph = None
        callback = RunCallbacks(self, run_id)
        current = self.snapshot(run_id)
        config = deepcopy(self.config)
        config.update(
            output_language=current["language"],
            max_debate_rounds=current["depth"],
            max_risk_discuss_rounds=current["depth"],
            checkpoint_enabled=current["checkpoint"],
            results_dir=str(self.root / run_id / "artifacts"),
        )
        self.update(
            run_id,
            status="running",
            started_at=_now(),
            event=("stage", _STAGE_LABELS["initializing"]),
        )
        final_state: dict[str, Any] = {}
        try:
            callback.validate_data_connections()
            factory = self.graph_factory
            if factory is None:
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                factory = TradingAgentsGraph
            credentials_context = (
                self.data_connection_context_provider(current.get("data_connections"))
                if self.data_connection_context_provider is not None
                else nullcontext()
            )
            with date_timezone_scope("America/New_York"), credentials_context, run_config(config):
                graph = factory(current["analysts"], config=config, callbacks=[callback])
                initial = graph.create_run_state(
                    current["ticker"], current["date"], current["asset_type"]
                )
                final_state.update(initial)
                args = graph.propagator.get_graph_args(callbacks=[callback])
                checkpoint_id = graph.begin_checkpoint(
                    current["ticker"], current["date"], current["asset_type"]
                )
                if checkpoint_id is not None:
                    args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = (
                        checkpoint_id
                    )
                self.stage(run_id, "analysts")
                for _messages, chunk in graph.stream_run(graph.checkpoint_input(initial), **args):
                    callback.validate_data_connections()
                    # Raw model/tool messages may include credentials or private provider errors.
                    # Reports and callback counters give the browser useful, safe progress.
                    if not isinstance(chunk, dict):
                        continue
                    final_state.update(chunk)
                    self._publish_reports(run_id, final_state)
                if not final_state.get("final_trade_decision"):
                    raise ValueError("Graph ended before its final decision")
                callback.validate_data_connections()
                # The existing writer escapes all generated HTML and disables raw HTML/scripts.
                graph.save_reports(
                    final_state,
                    current["ticker"],
                    save_path=self.root / run_id / "report",
                    html=True,
                )
                callback.validate_data_connections()
                graph.record_decision(current["ticker"], current["date"], final_state)
                graph.clear_checkpoint_on_success(
                    current["ticker"], current["date"], current["asset_type"]
                )
                stats = callback.get_stats()
                stats["elapsed_seconds"] = round(time.monotonic() - started, 1)
                callback.validate_data_connections()
                self.update(
                    run_id,
                    status="completed",
                    stage="completed",
                    finished_at=_now(),
                    rating=run_rating(final_state),
                    stats=stats,
                    report_url=f"/api/runs/{run_id}/report",
                    markdown_url=f"/api/runs/{run_id}/report.md",
                    event=("system", "分析已完成，报告已保存"),
                )
        except Exception as exc:
            stats = callback.get_stats()
            stats["elapsed_seconds"] = round(time.monotonic() - started, 1)
            self.update(
                run_id,
                status="failed",
                stage="failed",
                finished_at=_now(),
                stats=stats,
                error=_safe_error(exc),
                event=("error", _safe_error(exc)),
            )
        finally:
            if graph is not None:
                with suppress(Exception):
                    graph.end_checkpoint()
            with self.lock:
                if self.active_id == run_id:
                    self.active_id = None
                self.threads.pop(run_id, None)

    def _publish_reports(self, run_id: str, state: dict) -> None:
        reports = {
            key: state[key]
            for key in _REPORT_LABELS
            if isinstance(state.get(key), str) and state[key]
        }
        for group, keys in (
            ("investment_debate_state", ("bull_history", "bear_history")),
            (
                "risk_debate_state",
                ("aggressive_history", "conservative_history", "neutral_history"),
            ),
        ):
            value = state.get(group)
            if isinstance(value, dict):
                reports.update(
                    {
                        key: value[key]
                        for key in keys
                        if isinstance(value.get(key), str) and value[key]
                    }
                )
        with self.lock:
            previous = self.runs[run_id]["reports"]
            changed = [key for key in reports if previous.get(key) != reports[key]]
            if changed:
                self.update(
                    run_id,
                    reports=reports,
                    event=("report", "、".join(_REPORT_LABELS[key] for key in changed) + "已更新"),
                )
        if state.get("final_trade_decision"):
            self.stage(run_id, "portfolio")
        elif state.get("risk_debate_state", {}).get("history") or state.get(
            "trader_investment_plan"
        ):
            self.stage(run_id, "risk")
        elif state.get("investment_plan"):
            self.stage(run_id, "trading")
        elif state.get("investment_debate_state", {}).get("history"):
            self.stage(run_id, "research")


class RunCallbacks(BaseCallbackHandler):
    """Counts calls without publishing prompts, arguments or raw exceptions."""

    raise_error = True

    def __init__(self, store: RunStore, run_id: str):
        super().__init__()
        self.store, self.run_id = store, run_id
        self.data_snapshot = store.snapshot(run_id).get("data_connections")
        self.lock = threading.Lock()
        self.stats = {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}
        self.seen_llm: set[str] = set()

    def get_stats(self) -> dict:
        with self.lock:
            return dict(self.stats)

    def validate_data_connections(self):
        if self.store.data_connection_validator is not None and self.data_snapshot is not None:
            self.store.data_connection_validator(self.data_snapshot)

    def _publish(self, event: tuple[str, str] | None = None) -> None:
        self.validate_data_connections()
        stats = self.get_stats()
        stats["elapsed_seconds"] = 0
        self.store.update(self.run_id, stats=stats, event=event)

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.on_llm_start(serialized, [], **kwargs)

    def on_llm_start(self, serialized, prompts, **kwargs):
        node = (kwargs.get("metadata") or {}).get("langgraph_node")
        stage = {
            "Bull Researcher": "research",
            "Bear Researcher": "research",
            "Research Manager": "research",
            "Trader": "trading",
            "Aggressive Analyst": "risk",
            "Conservative Analyst": "risk",
            "Neutral Analyst": "risk",
            "Portfolio Manager": "portfolio",
        }.get(node)
        if stage:
            self.store.stage(self.run_id, stage)
        with self.lock:
            call_id = str(kwargs.get("run_id") or uuid.uuid4())
            if call_id in self.seen_llm:
                return
            self.seen_llm.add(call_id)
            self.stats["llm_calls"] += 1
        self._publish(("llm", "模型正在分析与推理"))

    def on_llm_end(self, response, **kwargs):
        usage = None
        try:
            message = response.generations[0][0].message
            usage = message.usage_metadata
        except (AttributeError, IndexError, TypeError):
            pass
        if not usage:
            output = getattr(response, "llm_output", None) or {}
            usage = output.get("token_usage") or output.get("usage") or {}
        with self.lock:
            self.stats["tokens_in"] += int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            )
            self.stats["tokens_out"] += int(
                usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            )
        self._publish()

    def on_tool_start(self, serialized, input_str, **kwargs):
        with self.lock:
            self.stats["tool_calls"] += 1
        # Never forward raw arguments or vendor responses into the browser event log.
        self._publish(("tool", "正在调用数据工具获取研究证据"))


def create_app(
    config: dict | None = None,
    graph_factory=None,
    *,
    workbench_dir: Path | None = None,
    secret_store=None,
    model_tester=None,
    instrument_search=None,
    market_provider=None,
    market_clock=None,
    alternative_providers=None,
    research_data_tester=None,
    durable_research: bool = False,
    authentication_required: bool = True,
) -> FastAPI:
    # The supported account mode always uses the scoped persistent queue.
    # Explicit legacy mode is retained for offline compatibility tests only.
    durable_research = durable_research or authentication_required
    store = RunStore(config if config is not None else DEFAULT_CONFIG, graph_factory)
    from types import SimpleNamespace

    from tradingagents.web.api import create_api_router
    from tradingagents.web.application.data_settings import DataSettingsService
    from tradingagents.web.application.market_alternatives import AlternativeMarketService
    from tradingagents.web.application.market_briefs import MarketBriefGenerator
    from tradingagents.web.application.market_data import MarketDataService
    from tradingagents.web.application.market_intelligence import (
        MarketIntelligenceService,
        ProfessionalMarketPulseService,
    )
    from tradingagents.web.application.models import ModelConfigurationService
    from tradingagents.web.application.research import ResearchService
    from tradingagents.web.application.research_context import ResearchContextService
    from tradingagents.web.application.research_data_connections import (
        ResearchDataConnectionService,
    )
    from tradingagents.web.application.research_evidence import ResearchEvidenceStore
    from tradingagents.web.application.runtime import create_selection_resolver
    from tradingagents.web.application.runtime_readiness import RuntimeReadinessService
    from tradingagents.web.application.themes import ThemesService
    from tradingagents.web.application.tracking import WatchlistTrackingService
    from tradingagents.web.application.user_services import UserServiceRegistry, build_user_services
    from tradingagents.web.application.viewpoints import ViewpointService
    from tradingagents.web.application.watchlists import WatchlistService
    from tradingagents.web.domain.research_workflow import research_workflow_definition
    from tradingagents.web.infrastructure.database import initialize_database
    from tradingagents.web.infrastructure.http_auth import (
        AccountAuthenticationMiddleware,
        SessionBoundResearch,
        current_request_identity,
    )

    database = initialize_database(store.root / "workbench.sqlite3")
    workspace = database.local_workspace()
    authentication = None
    if authentication_required:
        from tradingagents.web.application.authentication import AuthenticationService
        authentication = AuthenticationService(database)
        authentication.ensure_setup_token()
    else:
        from sqlalchemy import func, select

        from tradingagents.web.infrastructure.auth_models import AccountRecord
        with database.session() as session:
            if session.scalar(select(func.count()).select_from(AccountRecord)):
                database.dispose()
                raise RuntimeError("Cannot disable authentication for an initialized account store")
    model_service = ModelConfigurationService(
        database, workspace, secret_store=secret_store, tester=model_tester
    )
    watchlist_service = WatchlistService(
        database,
        workspace_id=workspace.workspace_id,
        owner_id=workspace.user_id,
        search_provider=instrument_search,
    )
    research_data_service = ResearchDataConnectionService(
        database, workspace, secret_store=secret_store, tester=research_data_tester
    )
    research_service = ResearchService(
        database,
        store.root / "research",
        config_snapshot_provider=lambda: deepcopy(store.config),
        data_snapshot_provider=research_data_service.freeze_for_run,
        max_active_runs_per_owner=3 if authentication_required else None,
        max_queued_runs_total=50 if authentication_required else None,
    )
    research_evidence_service = ResearchEvidenceStore(database, store.root / "research")
    readiness_service = RuntimeReadinessService(database, workspace, clock=market_clock)
    viewpoint_service = ViewpointService(database, workspace, research_service, clock=market_clock)
    store.data_connection_freezer = lambda: research_data_service.freeze_for_run(
        workspace.workspace_id, workspace.user_id
    )
    store.data_connection_context_provider = research_data_service.runtime_context_for_legacy
    store.data_connection_validator = research_data_service.validate_snapshot
    resolve_models = create_selection_resolver(
        model_service,
        local_status_reader=lambda: public_config(store.config, store.active_id),
    )
    market_service = MarketDataService(
        store.root / "market-cache", provider=market_provider, clock=market_clock
    )
    intelligence_service = MarketIntelligenceService(
        market_service, store.root / "market-snapshots", clock=market_clock
    )
    themes_service = ThemesService(database, workspace, market_service, clock=market_clock)
    themes_service.ensure_defaults()
    tracking_service = WatchlistTrackingService(
        database, workspace, market_service, clock=market_clock
    )
    alternative_service = AlternativeMarketService(
        database,
        workspace,
        store.root / "alternative-cache",
        secret_store=secret_store,
        providers=alternative_providers,
        clock=market_clock,
    )
    professional_pulse_service = ProfessionalMarketPulseService(
        alternative_service, store.root / "professional-pulse", clock=market_clock
    )
    research_context_service = ResearchContextService(
        database, workspace, store.root / "research-context", market=intelligence_service,
        themes=themes_service, alternative=alternative_service, clock=market_clock,
    )

    def select_models(request):
        bundle = scoped_services()
        chosen = dict(bundle.resolve_models(request))
        chosen["context_inputs"] = bundle.research_context_service.freeze(
            request.context_ids, ticker=request.ticker, analysis_date=request.date
        )
        return chosen
    market_brief_generator = MarketBriefGenerator(
        model_service,
        local_status_reader=lambda: public_config(store.config, active_run_id()),
        base_config=store.config,
    )

    def apply_data_settings(settings):
        market_service.minute_cache_ttl_seconds = settings.minute_cache_ttl_seconds
        market_service.daily_cache_ttl_seconds = settings.daily_cache_ttl_seconds
        return intelligence_service.configure(
            max_workers=settings.aggregation_max_workers,
            fetch_timeout_seconds=settings.aggregation_timeout_seconds,
            cache_seconds=settings.aggregation_refresh_seconds,
        )

    def invalidate_market_caches():
        themes_service.invalidate()
        tracking_service.invalidate()
        return intelligence_service.invalidate_market_data_cache()

    data_service = DataSettingsService(
        database,
        workspace,
        store.root / "market-cache",
        snapshot_cache_root=store.root / "market-snapshots",
        apply_callback=apply_data_settings,
        cache_invalidator=invalidate_market_caches,
        capabilities_reader=market_service.capabilities,
    )
    data_service.apply_saved_settings()

    local_services = SimpleNamespace(
        model_service=model_service, watchlist_service=watchlist_service,
        research_service=research_service, research_data_service=research_data_service,
        research_evidence_service=research_evidence_service, readiness_service=readiness_service,
        viewpoint_service=viewpoint_service, market_service=market_service,
        intelligence_service=intelligence_service, themes_service=themes_service,
        tracking_service=tracking_service, alternative_service=alternative_service,
        professional_pulse_service=professional_pulse_service,
        research_context_service=research_context_service, data_service=data_service,
        market_brief_generator=market_brief_generator, resolve_models=resolve_models,
    )
    user_services = UserServiceRegistry(workspace, local_services, lambda space: build_user_services(
        database, space, store.root, store.config, secret_store=secret_store,
        model_tester=model_tester, instrument_search=instrument_search, market_provider=market_provider,
        market_clock=market_clock, alternative_providers=alternative_providers,
        research_data_tester=research_data_tester,
    ))

    def current_scope():
        if not authentication_required:
            return workspace
        context = current_request_identity()
        if context is None:
            raise HTTPException(401, "请先登录。")
        return context.identity.workspace

    def scoped_services():
        return user_services.get(current_scope())

    def scoped_research():
        service = scoped_services().research_service
        context = current_request_identity()
        return SessionBoundResearch(service, authentication, context) if authentication_required else service

    def scoped_public_config():
        space = current_scope()
        if (space.workspace_id, space.user_id) == (workspace.workspace_id, workspace.user_id):
            return public_config(store.config, active_run_id())
        bundle = scoped_services()
        template = bundle.model_service.settings()["default_template"]
        profiles = {item["id"]: item for item in bundle.model_service.list_profiles()}
        profile = profiles.get(template["default_profile_id"]) if template else None
        connections = {item["id"]: item for item in bundle.model_service.list_connections()}
        connection = connections.get(profile["connection_id"]) if profile else None
        ready = bool(profile and connection and connection["enabled"])
        provider = profile["provider"] if profile else "unconfigured"
        services = []
        for source in bundle.research_data_service.list_connections():
            configured = source.enabled and (source.credential_configured or source.user_agent_configured)
            services.append({"id": source.provider, "name": source.name,
                "configured": configured, "status": "configured" if configured else "optional"})
        return {"version": tradingagents.__version__, "today": current_research_date(),
            "provider": provider, "quick_provider": provider, "deep_provider": provider,
            "quick_model": profile["model_id"] if profile else None,
            "deep_model": profile["model_id"] if profile else None,
            "output_language": template["language"] if template else "Chinese",
            "model_ready": ready, "services": services, "active_run_id": active_run_id()}

    def active_run_id():
        if not durable_research:
            return store.active_id
        from sqlalchemy import select

        from tradingagents.web.infrastructure.models import ResearchRun

        space = current_scope()
        with database.session() as session:
            return session.scalar(
                select(ResearchRun.id)
                .where(
                    ResearchRun.workspace_id == space.workspace_id,
                    ResearchRun.owner_id == space.user_id,
                    ResearchRun.status.in_(["queued", "running", "cancelling"]),
                )
                .order_by(ResearchRun.created_at)
                .limit(1)
            )

    def persistent_call(method, *args, **kwargs):
        from tradingagents.web.domain.tasks import ResearchError

        try:
            space = current_scope()
            return getattr(scoped_research(), method)(
                *args,
                workspace_id=space.workspace_id,
                owner_id=space.user_id,
                **kwargs,
            )
        except ResearchError as exc:
            raise HTTPException(exc.status_code, exc.message) from None

    def legacy_snapshot(run_id):
        if not durable_research:
            return store.snapshot(run_id)
        snapshot = persistent_call("get_run", _valid_id(run_id))
        snapshot["status"] = {
            "queued": "pending",
            "cancelling": "running",
            "cancelled": "interrupted",
        }.get(snapshot["status"], snapshot["status"])
        return snapshot

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            user_services.close()
            database.dispose()

    app = FastAPI(
        title="TradingAgents Local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.database = database
    app.state.model_service = model_service
    app.state.watchlist_service = watchlist_service
    app.state.research_service = research_service
    app.state.research_evidence_service = research_evidence_service
    app.state.readiness_service = readiness_service
    app.state.research_context_service = research_context_service
    app.state.viewpoint_service = viewpoint_service
    app.state.professional_pulse_service = professional_pulse_service
    app.state.market_service = market_service
    app.state.intelligence_service = intelligence_service
    app.state.data_service = data_service
    app.state.themes_service = themes_service
    app.state.alternative_service = alternative_service
    app.state.research_data_service = research_data_service
    app.state.tracking_service = tracking_service
    app.state.authentication_service = authentication
    app.state.authentication_required = authentication_required
    app.state.user_services = user_services
    ui_root = (workbench_dir or Path(__file__).parent / "ui").resolve()
    app.include_router(
        create_api_router(
            public_config_reader=scoped_public_config,
            workspace_reader=lambda: {
                "id": current_scope().workspace_id,
                "name": current_scope().name,
                "owner_id": current_scope().user_id,
            },
            model_service_reader=lambda: scoped_services().model_service,
            watchlist_service_reader=lambda: scoped_services().watchlist_service,
            capability_states={
                "model_connections": "available",
                "watchlists": "available",
                "charts": "available",
                "market_pulse": "available",
                "sector_attention": "available",
            },
            research_service_reader=scoped_research,
            scope_reader=current_scope,
            selection_resolver=select_models,
            market_service_reader=lambda: scoped_services().market_service,
            intelligence_service_reader=lambda: scoped_services().intelligence_service,
            brief_generator_reader=lambda: scoped_services().market_brief_generator,
            data_service_reader=lambda: scoped_services().data_service,
            themes_service_reader=lambda: scoped_services().themes_service,
            alternative_service_reader=lambda: scoped_services().alternative_service,
            research_data_service_reader=lambda: scoped_services().research_data_service,
            tracking_service_reader=lambda: scoped_services().tracking_service,
            workflow_reader=research_workflow_definition,
            research_evidence_service_reader=lambda: research_evidence_service,
            readiness_service_reader=lambda: scoped_services().readiness_service,
            research_context_service_reader=lambda: scoped_services().research_context_service,
            viewpoint_service_reader=lambda: scoped_services().viewpoint_service,
            professional_pulse_service_reader=lambda: scoped_services().professional_pulse_service,
            authentication_service_reader=(lambda: authentication) if authentication_required else None,
        )
    )
    if authentication_required:
        app.add_middleware(AccountAuthenticationMiddleware, authentication=authentication, service_registry=user_services)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            authority = urlsplit("//" + host)
            hostname = authority.hostname
            _ = authority.port
            if authority.username or authority.password or authority.path or hostname not in _HOSTS:
                return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        except ValueError:
            return JSONResponse({"detail": "无效的访问地址"}, status_code=403)
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{host}"
        # Browser extensions and links from another page can legitimately open
        # the local UI with Sec-Fetch-Site: cross-site. A top-level navigation
        # exposes its response to the user, while an external fetch could expose
        # local research data to another origin. Only the former is allowed.
        top_level_navigation = (
            request.method in {"GET", "HEAD"}
            and origin is None
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") == "document"
        )
        if (
            (origin is not None and origin != expected)
            or (
                request.headers.get("sec-fetch-site") in {"cross-site", "cross-origin"}
                and not top_level_navigation
            )
            or (request.method not in {"GET", "HEAD", "OPTIONS"} and origin != expected)
        ):
            return JSONResponse({"detail": "只接受本机页面发起的同源请求"}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "无效的请求长度"}, status_code=400)
        body_limit = (
            256 * 1024
            if request.url.path.startswith("/api/v1/themes")
            else 64 * 1024
            if request.url.path.startswith(("/api/v1/model-connections", "/api/v1/model-profiles"))
            else _MAX_BODY
        )
        if length > body_limit:
            return JSONResponse({"detail": "请求内容过长"}, status_code=413)
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and len(await request.body()) > body_limit
        ):
            return JSONResponse({"detail": "请求内容过长"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        is_report = bool(re.fullmatch(r"/api/(?:v1/)?runs/[0-9a-f-]{36}/report", request.url.path))
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if is_report else "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            + ("frame-ancestors 'self'" if is_report else "frame-ancestors 'none'")
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        if _request.url.path.startswith("/api/v1/"):
            return JSONResponse(
                {"error_code": "validation_error", "message": "输入参数有误", "retryable": False},
                status_code=422,
            )
        # Pydantic's default response includes the invalid input; do not echo it.
        errors = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "message": "字段格式或取值不符合要求",
            }
            for item in exc.errors()
        ]
        return JSONResponse({"detail": "输入参数有误", "errors": errors}, status_code=422)

    if authentication_required:
        from tradingagents.web.application.authentication import AuthError

        @app.exception_handler(AuthError)
        async def authentication_error(_request, error):
            return JSONResponse({"error_code": error.code, "message": error.message, "retryable": False},
                status_code=error.status_code)

    @app.get("/api/config")
    def get_config():
        return scoped_public_config()

    @app.get("/api/runs")
    def list_runs():
        if durable_research:
            page = persistent_call("list_runs", limit=100)
            return {
                "runs": [
                    {
                        **item,
                        "status": {
                            "queued": "pending",
                            "cancelling": "running",
                            "cancelled": "interrupted",
                        }.get(item["status"], item["status"]),
                    }
                    for item in page["items"]
                ]
            }
        return {"runs": store.history()}

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest):
        from tradingagents.web.infrastructure.recovery_policy import requires_explicit_model_binding

        if not durable_research and requires_explicit_model_binding(database):
            raise HTTPException(409, "恢复后请在新版研究页显式配置并检测模型模板，再启动研究。")
        if durable_research:
            from tradingagents.web.api.model_schemas import ModelConfigurationError
            from tradingagents.web.api.schemas import CreateResearchRequest

            body = CreateResearchRequest.model_validate(request.model_dump())
            try:
                selection = select_models(body)
            except ModelConfigurationError as exc:
                raise HTTPException(getattr(exc, "status_code", getattr(exc, "status", 409)), exc.message) from None
            snapshot = persistent_call("create_run", body, manifest=selection)
            return legacy_snapshot(snapshot["id"])
        return store.create(request)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return legacy_snapshot(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request):
        legacy_snapshot(run_id)
        try:
            revision = int(request.headers.get("last-event-id", "-1"))
        except ValueError:
            revision = -1

        async def stream():
            nonlocal revision
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                try:
                    snapshot = legacy_snapshot(run_id)
                except Exception as error:
                    if getattr(error, "status_code", None) in {401, 403, 404}:
                        return
                    raise
                if snapshot["revision"] != revision:
                    revision = snapshot["revision"]
                    yield f"id: {revision}\nevent: update\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                    heartbeat = time.monotonic()
                if snapshot["status"] in _TERMINAL:
                    yield "event: done\ndata: {}\n\n"
                    break
                if time.monotonic() - heartbeat >= 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    def report_file(run_id: str, suffix: str):
        snapshot = legacy_snapshot(run_id)
        if snapshot["status"] != "completed":
            raise HTTPException(404, "任务完成后可下载完整报告")
        file = (
            persistent_call("report_file", _valid_id(run_id), suffix)
            if durable_research
            else (store.root / _valid_id(run_id) / "report" / f"complete_report.{suffix}")
        )
        if not file.is_file() or file.is_symlink():
            raise HTTPException(404, "报告不存在")
        return FileResponse(
            file,
            media_type="text/html" if suffix == "html" else "text/markdown",
            filename=f"{snapshot['ticker']}_{snapshot['date']}.md" if suffix == "md" else None,
        )

    @app.get("/api/runs/{run_id}/report")
    def html_report(run_id: str):
        return report_file(run_id, "html")

    @app.get("/api/runs/{run_id}/report.md")
    def markdown_report(run_id: str):
        return report_file(run_id, "md")

    source_legacy = Path(__file__).resolve().parents[3] / "frontend" / "legacy"
    static = source_legacy if source_legacy.is_dir() else Path(__file__).parent / "static"
    app.mount("/assets", StaticFiles(directory=static, check_dir=False), name="assets")

    @app.get("/")
    def index():
        if authentication_required:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/workbench/", status_code=307)
        file = static / "index.html"
        if not file.is_file():
            raise HTTPException(503, "界面文件尚未安装")
        return FileResponse(file)

    @app.get("/workbench")
    def workbench_redirect():
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/workbench/", status_code=307)

    @app.get("/workbench/{resource:path}")
    def workbench(resource: str):
        # Built assets have a fixed base URL and never resolve user filesystem paths.
        root = ui_root
        target = (root / resource).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(404, "文件不存在")
        if resource and target.is_file():
            return FileResponse(target)
        if resource.startswith("assets/") or (resource and Path(resource).suffix):
            raise HTTPException(404, "文件不存在")
        index_file = root / "index.html"
        if not index_file.is_file():
            raise HTTPException(503, "新版工作台尚未构建，请执行 backend/scripts/package_workbench.py")
        return FileResponse(index_file)

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="TradingAgents local web interface")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("TRADINGAGENTS_WEB_PORT", "8000"))
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="Use an isolated results/state directory for the new workbench",
    )
    parser.add_argument(
        "--durable-research",
        action="store_true",
        help="Route legacy submissions into the same persistent research queue",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    config = deepcopy(DEFAULT_CONFIG)
    if args.state_dir is not None:
        config["results_dir"] = str(args.state_dir.expanduser().resolve())
    uvicorn.run(
        create_app(config, durable_research=args.durable_research),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
