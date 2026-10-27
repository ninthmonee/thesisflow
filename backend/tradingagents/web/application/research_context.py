"""Capture supplemental inputs only after an explicit user action, before research."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tradingagents.web.api.research_context_schemas import ResearchContextView
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.models import DataSnapshot, Membership

_KIND = "research_context_v1"
_LIMIT = 250_000
_NOTICE = (
    "用户主动附加的来源快照，仅作研究背景；主题归属不证明收入暴露，ETF申赎不代表个股资金流，"
    "期权与新闻仅覆盖返回样本。记录时刻、来源和内容哈希，不补齐缺失数据。"
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ResearchContextService:
    def __init__(self, database, workspace, root, *, market=None, themes=None, alternative=None, clock=None):
        self.database, self.workspace = database, workspace
        self.root = Path(root).resolve()
        self.market, self.themes, self.alternative = market, themes, alternative
        self.clock = clock or (lambda: datetime.now(UTC))

    def _authorize(self, session, *, write=False):
        role = session.scalar(select(Membership.role).where(
            Membership.workspace_id == self.workspace.workspace_id,
            Membership.user_id == self.workspace.user_id,
        ))
        if role is None:
            raise ResearchError("workspace_not_found", "工作区不存在", 404)
        if write and role not in {"owner", "researcher"}:
            raise ResearchError("research_forbidden", "当前权限不能附加研究资料", 403)

    def _row(self, session, context_id):
        row = session.scalar(select(DataSnapshot).where(
            DataSnapshot.id == str(context_id),
            DataSnapshot.workspace_id == self.workspace.workspace_id,
            DataSnapshot.kind == _KIND,
        ))
        if row is None or row.details.get("owner_id") != self.workspace.user_id:
            raise ResearchError("research_context_not_found", "附加资料不存在", 404)
        return row

    def _view(self, row):
        info = row.details
        return ResearchContextView(
            id=row.id, kind=info["kind"], symbol=info.get("symbol"), source=row.source,
            as_of=info.get("data_as_of"), received_at=row.received_at,
            content_hash=row.content_hash, summary=info["summary"], scope_notice=_NOTICE,
        )

    def get(self, context_id):
        with self.database.session() as session:
            self._authorize(session)
            return self._view(self._row(session, context_id))

    def create(self, body):
        with self.database.session() as session:
            self._authorize(session, write=True)
        provider = body.provider
        connection_id = str(body.connection_id) if body.connection_id else None
        symbol, data_as_of = body.symbol, None
        try:
            if body.kind == "market":
                if not body.market_snapshot_id or self.market is None:
                    raise ResearchError("market_snapshot_required", "请先加载并选择市场快照", 422)
                result = self.market.get_cached_snapshot(body.market_snapshot_id)
                source, data_as_of = "market_snapshot", result.as_of
                data = result.model_dump(mode="json")
                summary = f"市场观察 · {result.window} · {data_as_of or '时效未知'}"
            elif body.kind == "theme":
                if not body.theme_id or self.themes is None:
                    raise ResearchError("theme_required", "请选择主题及版本", 422)
                result = self.themes.get_theme(str(body.theme_id), version=body.theme_version)
                source, data_as_of = "user_theme", result.effective_from.isoformat()
                data = result.model_dump(mode="json")
                summary = f"主题 {result.name} · 版本 {result.version} · {len(result.members)} 个成员"
            elif body.kind == "options":
                if not symbol or self.alternative is None or provider not in {None, "yahoo", "massive", "alpaca"}:
                    raise ResearchError("options_context_invalid", "请选择证券及期权来源", 422)
                result = self.alternative.get_options(symbol, provider=provider or "yahoo",
                    connection_id=connection_id, expiration=body.expiration)
                source, data_as_of = result.meta.source, result.meta.as_of
                data = result.model_dump(mode="json")
                data["contracts"] = data["contracts"][:200]
                data["attached_contracts"] = len(data["contracts"])
                data["returned_contracts"] = len(result.contracts)
                summary = f"{symbol} 期权 · {len(result.contracts)} 份返回合约 · {result.meta.quality}"
            elif body.kind == "news":
                if self.alternative is None or provider not in {None, "massive"}:
                    raise ResearchError("news_context_invalid", "请选择已配置的新闻来源", 422)
                result = self.alternative.get_news_sentiment(provider="massive", connection_id=connection_id,
                    symbol=symbol, lookback_hours=body.lookback_hours, limit=100)
                source, data_as_of = result.meta.source, result.meta.as_of
                data = result.model_dump(mode="json")
                summary = f"{symbol or '新闻样本'} · {result.article_count} 篇 · {body.lookback_hours} 小时"
            else:
                if not symbol or self.alternative is None or provider not in {None, "ishares", "intrinio"}:
                    raise ResearchError("flows_context_invalid", "请选择ETF及净申赎来源", 422)
                result = self.alternative.get_etf_flows([symbol], provider=provider or "ishares",
                    connection_id=connection_id, lookback_days=body.lookback_days)
                source = provider or "ishares"
                stamps = [fund.meta.as_of for fund in result.funds if fund.meta.as_of]
                data_as_of = max(stamps) if stamps else None
                data = result.model_dump(mode="json")
                summary = f"{symbol} ETF份额/申赎 · {body.lookback_days} 天窗口"
        except ResearchError:
            raise
        except Exception as error:
            if hasattr(error, "code") and hasattr(error, "message"):
                raise ResearchError(error.code, error.message, getattr(error, "status_code", 422)) from None
            raise
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("context clock must include timezone")
        now = now.astimezone(UTC)
        packet = {"kind": body.kind, "symbol": symbol, "source": source, "as_of": data_as_of,
                  "received_at": now.isoformat(), "data": data, "notice": _NOTICE}
        raw = _json(packet).encode()
        if len(raw) > _LIMIT:
            raise ResearchError("research_context_too_large", "附加资料过大，请缩小数据窗口", 422)
        context_id = str(uuid4())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / f"{context_id}.json"
        with path.open("xb") as handle:
            path.chmod(0o600)
            handle.write(raw)
        try:
            with self.database.session() as session:
                self._authorize(session, write=True)
                row = DataSnapshot(id=context_id, workspace_id=self.workspace.workspace_id,
                    source=source, kind=_KIND, file_path=str(path), content_hash=hashlib.sha256(raw).hexdigest(),
                    as_of=now, received_at=now, details={"owner_id": self.workspace.user_id,
                        "kind": body.kind, "symbol": symbol, "data_as_of": data_as_of, "summary": summary,
                        "db_as_of_kind": "capture_time_only"})
                session.add(row)
                session.flush()
                return self._view(row)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def freeze(self, ids, *, ticker, analysis_date):
        """Authorize, verify stored bytes and time-cut off every selected reference."""
        packets = []
        with self.database.session() as session:
            self._authorize(session, write=True)
            for context_id in ids:
                row = self._row(session, context_id)
                path = Path(row.file_path)
                expected = self.root / f"{UUID(row.id)}.json"
                if path != expected or path.is_symlink() or not path.is_file() or path.stat().st_size > _LIMIT:
                    raise ResearchError("research_context_invalid", "附加资料文件无效，请重新附加", 409)
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != row.content_hash:
                    raise ResearchError("research_context_invalid", "附加资料校验失败，请重新附加", 409)
                packet = json.loads(raw)
                if packet["kind"] in {"options", "news"} and packet["symbol"] not in {None, ticker}:
                    raise ResearchError("research_context_symbol_mismatch", "附加资料证券与研究对象不一致", 422)
                stamp = packet.get("as_of")
                today = self.clock().astimezone(ZoneInfo("America/New_York")).date().isoformat()
                captured_date = datetime.fromisoformat(packet["received_at"]).astimezone(
                    ZoneInfo("America/New_York")
                ).date().isoformat()
                # Latest trade/publication dates do not prove when quotes or scores
                # became available. Current snapshots cannot backfill a past decision.
                if captured_date > analysis_date:
                    raise ResearchError("research_context_future", "资料捕获晚于研究日期；历史研究只能附加当时已留存的快照", 422)
                if stamp:
                    if str(stamp)[:10] > analysis_date:
                        raise ResearchError("research_context_future", "附加资料晚于研究日期，请移除或选择历史快照", 422)
                elif analysis_date < today:
                    raise ResearchError("research_context_time_unknown", "时效未知的资料不能用于历史研究", 422)
                date.fromisoformat(analysis_date)
                packets.append({"id": row.id, "hash": row.content_hash, **packet})
        return packets
