"""Private data connections and honest free-first alternative market observations."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from tradingagents.dataflows.vendors.market_alternatives import (
    ISSUER_FUNDS,
    AlpacaOptionsProvider,
    AlternativeVendorError,
    IntrinioFlowsProvider,
    IsharesHistoryProvider,
    MassiveProvider,
    YahooOptionsProvider,
    number,
    parse_occ_symbol,
)
from tradingagents.web.api.alternative_schemas import (
    AlternativeCapabilities,
    AlternativeDataError,
    AlternativeMeta,
    DataConnectionCreate,
    DataConnectionTestRequest,
    DataConnectionTestResponse,
    DataConnectionUpdate,
    DataConnectionView,
    ETFFlowPoint,
    ETFFlowSeries,
    ETFFlowsResponse,
    NewsSentimentArticle,
    NewsSentimentResponse,
    OptionContract,
    OptionsResponse,
)
from tradingagents.web.infrastructure.alternative_models import AlternativeDataConnection
from tradingagents.web.infrastructure.models import Membership, utc_now
from tradingagents.web.infrastructure.secrets import KeyringSecretStore, SecretStoreError


def _symbol(value):
    result = value.strip().upper()
    if not re.fullmatch(r"[A-Z^][A-Z0-9.^\-]{0,31}", result):
        raise AlternativeDataError("invalid_symbol", "请输入有效美股或ETF代码", 422)
    if (
        result.startswith("^")
        or ("." in result and not re.fullmatch(r"[A-Z0-9]+\.[AB]", result))
        or result.endswith(
            (
                "-USD",
                "-USDT",
                "-USDC",
                "-EUR",
                "-GBP",
                "-JPY",
                "-CAD",
                "-AUD",
                "-CHF",
                "-BTC",
                "-ETH",
            )
        )
    ):
        raise AlternativeDataError(
            "market_not_supported",
            "专业数据当前仅支持美股、美国上市ADR和ETF；不支持海外市场后缀、指数、外汇、期货或加密资产",
            422,
        )
    return result


def _timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(
                value / (1e9 if value > 1e15 else 1000 if value > 1e12 else 1), UTC
            )
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _nonnegative(value):
    result = number(value)
    return result if result is not None and result >= 0 else None


class AlternativeMarketService:
    def __init__(
        self,
        database,
        local_workspace,
        cache_root,
        *,
        secret_store=None,
        providers=None,
        clock=None,
    ):
        self.database = database
        self.workspace_id, self.owner_id = local_workspace.workspace_id, local_workspace.user_id
        self.root = Path(cache_root).expanduser().resolve()
        self.secrets = (
            secret_store
            if secret_store is not None
            else KeyringSecretStore("TradingAgents.Web.Data")
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.providers = (
            providers
            if providers is not None
            else {
                "ishares": IsharesHistoryProvider(self.root),
                "yahoo": YahooOptionsProvider(self.root),
                "massive": MassiveProvider(),
                "intrinio": IntrinioFlowsProvider(),
                "alpaca": AlpacaOptionsProvider(),
            }
        )
        self._lock = threading.RLock()
        self._locks = {}

    def now(self):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("alternative market clock must have timezone")
        return now.astimezone(UTC)

    def _authorize(self, session, *, write=False):
        role = session.scalar(
            select(Membership.role).where(
                Membership.workspace_id == self.workspace_id, Membership.user_id == self.owner_id
            )
        )
        if role is None or (write and role != "owner"):
            raise AlternativeDataError(
                "data_connection_forbidden", "当前用户无权执行该数据连接操作", 403
            )

    @staticmethod
    def _view(row):
        return DataConnectionView(
            id=row.id,
            name=row.name,
            provider=row.provider,
            feed=row.feed,
            enabled=row.enabled,
            revision=row.revision,
            credential_configured=bool(row.credential_ref),
            secondary_credential_configured=bool(row.secondary_credential_ref),
            test_results=deepcopy(row.test_results),
            tested_at=row.tested_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _owned(self, session, connection_id):
        row = session.scalar(
            select(AlternativeDataConnection).where(
                AlternativeDataConnection.id == connection_id,
                AlternativeDataConnection.workspace_id == self.workspace_id,
                AlternativeDataConnection.owner_id == self.owner_id,
            )
        )
        if row is None:
            raise AlternativeDataError("data_connection_not_found", "数据连接不存在", 404)
        return row

    def _new_secret(self, value):
        raw = value.get_secret_value().strip()
        if not raw or len(raw) > 8192 or "\n" in raw or "\r" in raw:
            raise AlternativeDataError(
                "invalid_data_credential", "数据凭据不能为空、过长或包含换行", 422
            )
        reference = f"data/{self.workspace_id}/{self.owner_id}/{uuid4()}"
        try:
            self.secrets.set(reference, raw)
        except SecretStoreError:
            raise AlternativeDataError(
                "secret_store_unavailable", "系统密钥库不可用，未写入明文文件", 503
            ) from None
        return reference

    def _discard(self, reference):
        if reference:
            with suppress(SecretStoreError):
                self.secrets.delete(reference)

    def _secret(self, reference):
        if not reference or not reference.startswith(f"data/{self.workspace_id}/{self.owner_id}/"):
            raise AlternativeDataError("credential_scope", "数据凭据范围无效", 403)
        try:
            value = self.secrets.get(reference)
        except SecretStoreError:
            raise AlternativeDataError(
                "secret_store_unavailable", "系统密钥库不可用", 503
            ) from None
        if not value:
            raise AlternativeDataError("credential_missing", "数据凭据不存在，请重新保存连接", 409)
        return value

    def list_connections(self):
        with self.database.session() as session:
            self._authorize(session)
            rows = session.scalars(
                select(AlternativeDataConnection)
                .where(
                    AlternativeDataConnection.workspace_id == self.workspace_id,
                    AlternativeDataConnection.owner_id == self.owner_id,
                )
                .order_by(AlternativeDataConnection.created_at)
            ).all()
            return [self._view(row) for row in rows]

    def create_connection(self, body):
        body = DataConnectionCreate.model_validate(body)
        if body.provider == "alpaca" and body.api_secret is None:
            raise AlternativeDataError(
                "secondary_credential_required", "Alpaca需要Key ID和Secret Key", 422
            )
        if body.provider != "alpaca" and body.api_secret is not None:
            raise AlternativeDataError(
                "secondary_credential_not_supported",
                "此供应商只接收API Key，请勿填写其他服务的Secret",
                422,
            )
        references = []
        try:
            with self._lock, self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._authorize(session, write=True)
                references.append(self._new_secret(body.api_key))
                if body.api_secret is not None:
                    references.append(self._new_secret(body.api_secret))
                row = AlternativeDataConnection(
                    id=str(uuid4()),
                    workspace_id=self.workspace_id,
                    owner_id=self.owner_id,
                    name=body.name.strip(),
                    provider=body.provider,
                    feed=body.feed,
                    credential_ref=references[0],
                    secondary_credential_ref=references[1] if len(references) > 1 else None,
                    enabled=body.enabled,
                    test_results={},
                    revision=1,
                )
                if not row.name:
                    raise AlternativeDataError("invalid_name", "请输入连接名称", 422)
                session.add(row)
                session.flush()
                result = self._view(row)
            return result
        except Exception:
            for reference in references:
                self._discard(reference)
            raise

    def update_connection(self, connection_id, body):
        body = DataConnectionUpdate.model_validate(body)
        created, replaced = [], []
        try:
            with self._lock, self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._authorize(session, write=True)
                row = self._owned(session, connection_id)
                if body.revision != row.revision:
                    raise AlternativeDataError(
                        "revision_conflict", "数据连接已更新，请刷新后重试", 409
                    )
                if row.provider != "alpaca" and body.api_secret is not None:
                    raise AlternativeDataError(
                        "secondary_credential_not_supported", "此供应商不使用API Secret", 422
                    )
                for field, secret in (
                    ("credential_ref", body.api_key),
                    ("secondary_credential_ref", body.api_secret),
                ):
                    if secret is not None:
                        reference = self._new_secret(secret)
                        created.append(reference)
                        replaced.append(getattr(row, field))
                        setattr(row, field, reference)
                if body.name is not None:
                    row.name = body.name.strip()
                    if not row.name:
                        raise AlternativeDataError("invalid_name", "请输入连接名称", 422)
                if body.feed is not None:
                    row.feed = body.feed
                if body.enabled is not None:
                    row.enabled = body.enabled
                if created or body.feed is not None:
                    row.test_results, row.tested_at = {}, None
                row.revision += 1
                row.updated_at = utc_now()
                session.flush()
                result = self._view(row)
        except Exception:
            for reference in created:
                self._discard(reference)
            raise
        for reference in replaced:
            self._discard(reference)
        return result

    def _connection(self, provider, connection_id=None):
        with self.database.session() as session:
            self._authorize(session)
            if connection_id:
                row = self._owned(session, connection_id)
            else:
                row = session.scalar(
                    select(AlternativeDataConnection)
                    .where(
                        AlternativeDataConnection.workspace_id == self.workspace_id,
                        AlternativeDataConnection.owner_id == self.owner_id,
                        AlternativeDataConnection.provider == provider,
                        AlternativeDataConnection.enabled.is_(True),
                    )
                    .order_by(AlternativeDataConnection.created_at.desc())
                    .limit(1)
                )
            if row is None:
                raise AlternativeDataError(
                    "data_configuration_required",
                    "请先保存已有账号的数据API连接；不会自动购买订阅",
                    409,
                )
            if row.provider != provider or not row.enabled:
                raise AlternativeDataError(
                    "data_connection_disabled", "数据连接已停用或供应商不匹配", 409
                )
            return {
                "id": row.id,
                "revision": row.revision,
                "provider": row.provider,
                "feed": row.feed,
                "key": self._secret(row.credential_ref),
                "secret": self._secret(row.secondary_credential_ref)
                if row.secondary_credential_ref
                else None,
            }

    def _meta(
        self,
        source,
        feed,
        *,
        as_of=None,
        quality="complete",
        estimated=False,
        warnings=None,
        coverage=None,
        scope="",
    ):
        return AlternativeMeta(
            source=source,
            feed=feed,
            as_of=as_of,
            received_at=self.now(),
            quality=quality,
            delay_label={
                "yahoo": "Yahoo未承诺实时延迟；期权报价时刻未知",
                "ishares": "发行商日级文件，非盘中资金流",
                "massive": "套餐与字段决定延迟，新闻按官方说明每小时更新",
                "alpaca": "indicative为派生指示性数据；不代表OPRA真实报价",
                "intrinio": "供应商日级资金数据；文件日与有效日分开",
            }.get(source, "来源未承诺实时延迟"),
            is_estimated=estimated,
            warnings=warnings or [],
            coverage=coverage or {},
            scope=scope,
        )

    def _cached(self, identity, ttl, model, load, *, refresh=False):
        key = hashlib.sha256(
            json.dumps(
                ["alternatives-v2-closed-sessions", self.workspace_id, self.owner_id, identity],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        path = self.root / "responses" / f"{key}.json"
        with self._lock:
            lock = self._locks.setdefault(key, threading.RLock())
        with lock:
            cached = None
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 12_000_000:
                    cached = model.model_validate_json(path.read_text())
            except (ValueError, OSError):
                pass
            meta = cached.meta if cached is not None else None
            if not refresh and meta and 0 <= (self.now() - meta.received_at).total_seconds() < ttl:
                return cached.model_copy(
                    update={"meta": meta.model_copy(update={"cache": "hit"})}, deep=True
                )
            try:
                result = load()
            except AlternativeVendorError as error:
                if not refresh and cached is not None and error.status_code in {429, 503}:
                    return cached.model_copy(
                        update={
                            "meta": cached.meta.model_copy(
                                update={
                                    "cache": "stale",
                                    "quality": "stale",
                                    "warnings": [
                                        *cached.meta.warnings,
                                        "上游当前不可用，返回已标记过期的缓存。",
                                    ],
                                }
                            )
                        },
                        deep=True,
                    )
                messages = {
                    "authentication_failed": "数据凭据无效，请重新检测连接",
                    "data_permission_required": "账号未获所选端点或feed权限；未自动升级付费",
                    "rate_limited": "数据源请求额度已用尽，请稍后重试",
                    "issuer_symbol_not_supported": "当前免费发行商适配尚未覆盖此ETF",
                    "expiration_not_available": "所选到期日不在来源可用范围",
                    "unsafe_pagination": "来源分页地址未通过固定端点校验",
                    "source_schema_changed": "来源响应结构发生变化或缺少有效字段",
                }
                raise AlternativeDataError(
                    error.code,
                    messages.get(error.code, "数据源暂时不可用；未使用模拟数据替代"),
                    error.status_code,
                ) from None
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(f".{uuid4()}.tmp")
            try:
                temporary.write_text(result.model_dump_json(), encoding="utf-8")
                temporary.chmod(0o600)
                temporary.replace(path)
            except OSError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
            return result

    def capabilities(self):
        configured = {row.provider for row in self.list_connections() if row.enabled}
        definitions = [
            (
                "yahoo",
                "Yahoo公开期权",
                ["options"],
                "none",
                True,
                "公开期权字段；没有Greeks；报价时刻和OI日期可能未知",
                "https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.option_chain.html",
            ),
            (
                "ishares",
                "iShares发行商历史文件",
                ["etf_flows"],
                "none",
                True,
                "同文件NAV/份额变化估算；异常变动需核对；仅个人非商业用途",
                "https://www.ishares.com/us/products/239705/ishares-phlx-semiconductor-etf",
            ),
            (
                "massive",
                "Massive已有账户",
                ["news", "options"],
                "api_key",
                "massive" in configured,
                "Stocks Basic新闻免费；Options Basic不含链快照，权限取决于已有套餐",
                "https://massive.com/docs/rest/stocks/news",
            ),
            (
                "intrinio",
                "Intrinio ETF Global已有账户",
                ["etf_flows"],
                "api_key",
                "intrinio" in configured,
                "需要账号已有ETF Global权限；不会自动购买或试用",
                "https://docs.intrinio.com/documentation/web_api/get_etf_global_etf_fund_flows_v2",
            ),
            (
                "alpaca",
                "Alpaca已有账户",
                ["options"],
                "key_and_secret",
                "alpaca" in configured,
                "Basic可用indicative派生数据；OPRA需另有权限",
                "https://docs.alpaca.markets/us/reference/optionchain",
            ),
        ]
        return AlternativeCapabilities(
            sources=[
                {
                    "id": identifier,
                    "name": name,
                    "capabilities": capabilities,
                    "authentication": auth,
                    "configured": available,
                    "free_or_existing_account": True,
                    "scope_notice": scope,
                    "documentation_url": url,
                }
                for identifier, name, capabilities, auth, available, scope, url in definitions
            ],
            issuer_etfs=list(ISSUER_FUNDS),
        )

    def _issuer_flows(self, symbol, lookback_days):
        response = self.providers["ishares"].history(symbol)
        now = self.now()
        local_day = now.astimezone(ZoneInfo("America/New_York")).date()
        start = local_day - timedelta(days=lookback_days)
        rows = [
            row
            for row in response["rows"]
            if start - timedelta(days=10) <= date.fromisoformat(row["date"]) <= local_day
        ]
        if not rows:
            return ETFFlowSeries(
                symbol=symbol,
                points=[],
                meta=self._meta(
                    "ishares", "issuer_historical_xml", quality="unavailable", estimated=True
                ),
            )
        import exchange_calendars

        calendar = exchange_calendars.get_calendar(
            "XNYS", start=start - timedelta(days=15), end=local_day + timedelta(days=7)
        )
        sessions = [
            stamp.strftime("%Y-%m-%d")
            for stamp in calendar.sessions_in_range(start - timedelta(days=10), local_day)
            if calendar.session_close(stamp).to_pydatetime() <= now
        ]
        session_index = {value: index for index, value in enumerate(sessions)}
        rows = sorted(
            (row for row in rows if row["date"] in session_index), key=lambda row: row["date"]
        )
        points, previous = [], None
        for row in rows:
            flow, quality, reason = None, "partial", "缺少相邻完整交易日的有效NAV和份额"
            nav, shares = (
                number(row["nav"], positive=True),
                number(row["shares_outstanding"], positive=True),
            )
            if (
                previous
                and nav is not None
                and shares is not None
                and previous["nav"]
                and previous["shares_outstanding"]
            ):
                same_window = session_index[row["date"]] - session_index[previous["date"]] == 1
                shares_ratio, nav_ratio = (
                    shares / previous["shares_outstanding"],
                    nav / previous["nav"],
                )
                if not 0.75 <= shares_ratio <= 1.25 or not 0.65 <= nav_ratio <= 1.5:
                    quality, reason = (
                        "action_required",
                        "份额或NAV变动异常，需核对拆股、合并或大额创赎；未推算资金流",
                    )
                elif same_window:
                    flow, quality, reason = (
                        (shares - previous["shares_outstanding"]) * previous["nav"],
                        "complete",
                        None,
                    )
            if date.fromisoformat(row["date"]) >= start:
                points.append(
                    ETFFlowPoint(
                        date=row["date"],
                        as_of_date=row["date"],
                        nav=nav,
                        shares_outstanding=shares,
                        net_flow_usd=flow,
                        flow_kind="estimated",
                        quality=quality,
                        method="同一发行商文件、相邻XNYS交易日：(本日份额−前日份额)×前日NAV；异常变动暂停，不额外假设拆股已处理",
                        reason=reason,
                    )
                )
            previous = {"date": row["date"], "nav": nav, "shares_outstanding": shares}
        quality = (
            "unavailable"
            if not points
            else "action_required"
            if any(point.quality == "action_required" for point in points)
            else "partial"
            if any(point.quality != "complete" for point in points)
            else "complete"
        )
        return ETFFlowSeries(
            symbol=symbol,
            points=points,
            meta=self._meta(
                "ishares",
                "issuer_historical_xml",
                as_of=points[-1].as_of_date if points else None,
                quality=quality,
                estimated=True,
                warnings=[
                    "该值为本机计算的份额变化估算，不是发行商已报告净申赎，也不代表机构资金。",
                    "只在同一份历史文件内计算；拆股检查为异常守卫，不能替代完整公司行动核验。",
                    "发行商文件仅供个人非商业使用，原始版权说明保留在本机。",
                ],
                coverage={
                    "points": len(points),
                    "last_closed_session": sessions[-1] if sessions else None,
                    "cutoff_policy": "XNYS.session_close <= received_at; window dates use America/New_York",
                    "raw_sha256": response.get("raw_sha256"),
                    "corporate_action_check": "anomaly_guard_only",
                },
                scope="已适配iShares基金；日级历史估算",
            ),
        )

    def get_etf_flows(
        self, symbols, *, provider="ishares", connection_id=None, lookback_days=30, _refresh=False
    ):
        with self.database.session() as session:
            self._authorize(session)
        symbols = list(dict.fromkeys(_symbol(value) for value in symbols))
        if (
            not 1 <= len(symbols) <= 10
            or not 1 <= lookback_days <= 365
            or provider not in {"ishares", "intrinio"}
        ):
            raise AlternativeDataError(
                "invalid_flow_request", "ETF数量须1–10且历史窗口须1–365日", 422
            )
        connection = self._connection(provider, connection_id) if provider != "ishares" else None
        funds = []
        for symbol in symbols:

            def load(symbol=symbol):
                if provider == "ishares":
                    return self._issuer_flows(symbol, lookback_days)
                result = self.providers["intrinio"].flows(
                    connection["key"],
                    symbol,
                    since=(self.now().date() - timedelta(days=lookback_days)).isoformat(),
                    until=self.now().date().isoformat(),
                )
                points = []
                for row in result["rows"]:
                    try:
                        day = date.fromisoformat(row["date"]).isoformat()
                        effective = (
                            date.fromisoformat(row["as_of_date"]).isoformat()
                            if row.get("as_of_date")
                            else None
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
                    value = number(row.get("fund_flow"))
                    points.append(
                        ETFFlowPoint(
                            date=day,
                            as_of_date=effective,
                            nav=number(row.get("nav"), positive=True),
                            shares_outstanding=number(row.get("shares_outstanding"), positive=True),
                            net_flow_usd=value,
                            flow_kind="vendor_reported",
                            quality="complete" if value is not None and effective else "partial",
                            method="Intrinio ETF Global fund_flow原字段；不是根据成交量推断",
                            reason=None
                            if value is not None and effective
                            else "供应商净流量或有效日期字段缺失",
                        )
                    )
                points.sort(key=lambda point: point.as_of_date or point.date)
                return ETFFlowSeries(
                    symbol=symbol,
                    points=points,
                    meta=self._meta(
                        "intrinio",
                        "etf_global_fund_flows",
                        as_of=points[-1].as_of_date if points else None,
                        quality="unavailable"
                        if not points
                        else "partial"
                        if result.get("truncated")
                        or any(point.quality != "complete" for point in points)
                        else "complete",
                        warnings=[
                            "文件日date和有效日as_of_date可能不同；账号需具有ETF Global端点权限。"
                        ],
                        coverage={
                            "points": len(points),
                            "truncated": bool(result.get("truncated")),
                        },
                        scope="供应商ETF日级创设赎回资金流",
                    ),
                )

            funds.append(
                self._cached(
                    [
                        "flows",
                        symbol,
                        provider,
                        connection_id or (connection or {}).get("id"),
                        (connection or {}).get("revision"),
                        lookback_days,
                    ],
                    6 * 3600,
                    ETFFlowSeries,
                    load,
                    refresh=_refresh,
                )
            )
        return ETFFlowsResponse(funds=funds)

    def get_options(
        self,
        symbol,
        *,
        provider="yahoo",
        connection_id=None,
        expiration=None,
        max_expirations=1,
        _refresh=False,
    ):
        with self.database.session() as session:
            self._authorize(session)
        symbol = _symbol(symbol)
        if provider not in {"yahoo", "massive", "alpaca"} or not 1 <= max_expirations <= 3:
            raise AlternativeDataError("invalid_options_request", "期权来源或到期日范围无效", 422)
        if expiration is not None:
            try:
                if date.fromisoformat(expiration) < self.now().date():
                    raise ValueError
            except (TypeError, ValueError):
                raise AlternativeDataError(
                    "invalid_expiration", "到期日须为当前或未来有效日期", 422
                ) from None
        connection = self._connection(provider, connection_id) if provider != "yahoo" else None

        def load():
            if provider == "yahoo":
                result = self.providers[provider].options(
                    symbol, expiration=expiration, max_expirations=max_expirations
                )
            elif provider == "alpaca":
                result = self.providers[provider].options(
                    connection["key"],
                    connection["secret"],
                    symbol,
                    feed=connection["feed"],
                    expiration=expiration,
                    max_expirations=max_expirations,
                )
            else:
                result = self.providers[provider].options(
                    connection["key"],
                    symbol,
                    expiration=expiration,
                    max_expirations=max_expirations,
                )
            contracts = []
            if provider == "yahoo":
                for row in result["contracts"]:
                    strike = _nonnegative(row.get("strike"))
                    if strike is None:
                        continue
                    contracts.append(
                        OptionContract(
                            symbol=str(row.get("contractSymbol", "")),
                            expiration=row["expiration"],
                            contract_type=row["contract_type"],
                            strike=strike,
                            multiplier=None,
                            bid=_nonnegative(row.get("bid")),
                            ask=_nonnegative(row.get("ask")),
                            last_price=_nonnegative(row.get("lastPrice")),
                            volume=_nonnegative(row.get("volume")),
                            open_interest=_nonnegative(row.get("openInterest")),
                            implied_volatility=_nonnegative(row.get("impliedVolatility")),
                            last_trade_at=_timestamp(row.get("lastTradeDate")),
                        )
                    )
            elif provider == "massive":
                for row in result["rows"]:
                    details, day, greeks = (
                        row.get("details") or {},
                        row.get("day") or {},
                        row.get("greeks") or {},
                    )
                    quote, trade = row.get("last_quote") or {}, row.get("last_trade") or {}
                    strike = _nonnegative(details.get("strike_price"))
                    if strike is None or details.get("contract_type") not in {"call", "put"}:
                        continue
                    contracts.append(
                        OptionContract(
                            symbol=str(details.get("ticker", "")),
                            expiration=details["expiration_date"],
                            contract_type=details["contract_type"],
                            strike=strike,
                            multiplier=_nonnegative(details.get("shares_per_contract")),
                            bid=_nonnegative(quote.get("bid")),
                            ask=_nonnegative(quote.get("ask")),
                            last_price=_nonnegative(trade.get("price")),
                            volume=_nonnegative(day.get("volume")),
                            open_interest=_nonnegative(row.get("open_interest")),
                            implied_volatility=_nonnegative(row.get("implied_volatility")),
                            delta=number(greeks.get("delta")),
                            gamma=number(greeks.get("gamma")),
                            theta=number(greeks.get("theta")),
                            vega=number(greeks.get("vega")),
                            quote_as_of=_timestamp(quote.get("last_updated")),
                            last_trade_at=_timestamp(trade.get("sip_timestamp")),
                        )
                    )
            else:
                for name, row in result["rows"].items():
                    parsed = parse_occ_symbol(name)
                    if parsed is None:
                        continue
                    quote, trade, greeks = (
                        row.get("latestQuote") or {},
                        row.get("latestTrade") or {},
                        row.get("greeks") or {},
                    )
                    contracts.append(
                        OptionContract(
                            symbol=name,
                            expiration=parsed[0],
                            contract_type=parsed[1],
                            strike=parsed[2],
                            bid=_nonnegative(quote.get("bp")),
                            ask=_nonnegative(quote.get("ap")),
                            last_price=_nonnegative(trade.get("p")),
                            implied_volatility=_nonnegative(row.get("impliedVolatility")),
                            delta=number(greeks.get("delta")),
                            gamma=number(greeks.get("gamma")),
                            theta=number(greeks.get("theta")),
                            vega=number(greeks.get("vega")),
                            quote_as_of=_timestamp(quote.get("t")),
                            last_trade_at=_timestamp(trade.get("t")),
                        )
                    )

            def ratio(field):
                puts = [getattr(item, field) for item in contracts if item.contract_type == "put"]
                calls = [getattr(item, field) for item in contracts if item.contract_type == "call"]
                if (
                    not puts
                    or not calls
                    or any(value is None for value in [*puts, *calls])
                    or sum(calls) == 0
                ):
                    return None
                return sum(puts) / sum(calls)

            times = [item.last_trade_at for item in contracts if item.last_trade_at is not None]
            warnings = [
                "仅汇总本次所选到期日和返回合约；不是全市场put/call，也不表示交易方向。",
                "OI、IV与Greeks按来源原字段显示；缺失不计算或补零。",
            ]
            if provider == "yahoo":
                warnings += [
                    "Yahoo未提供可核实的报价时刻/OI日期或Greeks；保留未知，不以最后交易时间冒充报价时间。",
                    "公开免费接口可限流或改变，不承诺OPRA/NBBO或实时覆盖。",
                ]
            if provider == "alpaca":
                warnings.append(
                    "indicative报价为派生指示值、成交延迟15分钟；只有账号已授权OPRA才能选择opra。"
                    if connection["feed"] == "indicative"
                    else "已显式请求OPRA；权限及实际覆盖以账号和返回字段为准。"
                )
            feed = (
                "yahoo_public_options"
                if provider == "yahoo"
                else connection["feed"]
                if provider == "alpaca"
                else "massive_options_snapshot"
            )
            missing = {
                "contracts_missing_quote_or_time": sum(
                    item.bid is None or item.ask is None or item.quote_as_of is None
                    for item in contracts
                ),
                "contracts_missing_iv": sum(item.implied_volatility is None for item in contracts),
                "contracts_missing_oi_or_date": sum(
                    item.open_interest is None or item.open_interest_as_of is None
                    for item in contracts
                ),
                "contracts_missing_greeks": sum(
                    any(
                        getattr(item, field) is None
                        for field in ("delta", "gamma", "theta", "vega")
                    )
                    for item in contracts
                ),
            }
            meta = self._meta(
                provider,
                feed,
                as_of=max(times).isoformat() if times else None,
                quality="unavailable"
                if not contracts
                else "partial"
                if result.get("truncated")
                or any(missing.values())
                or provider in {"yahoo", "alpaca"}
                else "complete",
                warnings=warnings,
                coverage={
                    "returned_contracts": len(contracts),
                    "selected_expirations": len(result["selected_expirations"]),
                    "truncated": bool(result.get("truncated")),
                    "as_of_kind": "latest_returned_trade",
                    **missing,
                },
                scope="所选标的、到期日和返回合约的快照",
            )
            if provider == "alpaca" and connection["feed"] == "opra":
                meta.delay_label = "已请求账号授权的OPRA feed，字段时刻分别展示"
            elif provider == "massive":
                meta.delay_label = (
                    "Options Starter/Developer延迟15分钟，Advanced实时；权限取决于已有套餐"
                )
            return OptionsResponse(
                underlying=symbol,
                available_expirations=result["expirations"],
                selected_expirations=result["selected_expirations"],
                contracts=contracts,
                put_call_volume_ratio=ratio("volume"),
                put_call_oi_ratio=ratio("open_interest"),
                meta=meta,
            )

        return self._cached(
            [
                "options",
                symbol,
                provider,
                (connection or {}).get("id"),
                (connection or {}).get("revision"),
                expiration,
                max_expirations,
            ],
            300,
            OptionsResponse,
            load,
            refresh=_refresh,
        )

    def get_news_sentiment(
        self,
        *,
        provider="massive",
        connection_id=None,
        symbol=None,
        lookback_hours=24,
        limit=100,
        _refresh=False,
    ):
        if provider != "massive" or not 1 <= lookback_hours <= 168 or not 1 <= limit <= 1000:
            raise AlternativeDataError("invalid_news_request", "新闻来源或窗口无效", 422)
        symbol = _symbol(symbol) if symbol else None
        connection = self._connection(provider, connection_id)

        def load():
            since = self.now() - timedelta(hours=lookback_hours)
            result = self.providers[provider].news(
                connection["key"],
                symbol=symbol,
                since=since.isoformat(),
                until=self.now().isoformat(),
                limit=limit,
            )
            unique, observations = {}, set()
            for row in result["rows"]:
                stamp = _timestamp(row.get("published_utc"))
                if stamp is None or not since <= stamp <= self.now():
                    continue
                identifier = str(row.get("id") or row.get("article_url") or "")
                if not identifier or identifier in unique or not isinstance(row.get("title"), str):
                    continue
                counts = {"positive": 0, "negative": 0, "neutral": 0}
                insights = row.get("insights") if isinstance(row.get("insights"), list) else []
                for insight in insights:
                    if not isinstance(insight, dict):
                        continue
                    ticker, sentiment = insight.get("ticker"), insight.get("sentiment")
                    if (
                        not isinstance(ticker, str)
                        or sentiment not in counts
                        or (symbol and ticker != symbol)
                    ):
                        continue
                    identity = identifier, ticker
                    if identity not in observations:
                        observations.add(identity)
                        counts[sentiment] += 1
                try:
                    parsed = urlsplit(row.get("article_url") or "")
                    safe_url = (
                        row.get("article_url")
                        if parsed.scheme in {"http", "https"}
                        and parsed.hostname
                        and parsed.username is None
                        and parsed.password is None
                        else None
                    )
                except (ValueError, TypeError):
                    safe_url = None
                publisher = row.get("publisher") if isinstance(row.get("publisher"), dict) else {}
                tickers = row.get("tickers") if isinstance(row.get("tickers"), list) else []
                unique[identifier] = NewsSentimentArticle(
                    id=identifier,
                    title=row["title"][:1000],
                    url=safe_url,
                    publisher=str(publisher.get("name") or "未知来源")[:240],
                    published_at=stamp,
                    tickers=[value for value in tickers if isinstance(value, str)][:100],
                    positive_observations=counts["positive"],
                    negative_observations=counts["negative"],
                    neutral_observations=counts["neutral"],
                    missing_sentiment=sum(counts.values()) == 0,
                )
            articles = sorted(unique.values(), key=lambda row: row.published_at, reverse=True)
            positive, negative, neutral = [
                sum(getattr(row, f"{name}_observations") for row in articles)
                for name in ("positive", "negative", "neutral")
            ]
            scored = positive + negative + neutral
            return NewsSentimentResponse(
                articles=articles,
                article_count=len(articles),
                publisher_count=len({row.publisher for row in articles}),
                ticker_count=len({ticker for _, ticker in observations}),
                positive=positive,
                negative=negative,
                neutral=neutral,
                unclassified_articles=sum(row.missing_sentiment for row in articles),
                sentiment_balance=(positive - negative) / scored if scored else None,
                meta=self._meta(
                    provider,
                    "ticker_news_insights",
                    as_of=articles[0].published_at.isoformat() if articles else None,
                    quality="unavailable" if not articles else "partial",
                    warnings=[
                        "这是供应商可获取新闻样本，不代表全部美股公司、媒体或全市场投资者情绪。",
                        "官方端点按小时更新；文章发布时间不等于本系统或供应商收到/评分时间，禁止据此做无偏历史回测。",
                        "缺失情绪不当作中性；去重后按ticker观察数统计，新闻情绪不能直接推断资金流。",
                    ],
                    coverage={
                        "truncated": bool(result.get("truncated")),
                        "request_limit": limit,
                        "lookback_hours": lookback_hours,
                        "as_of_kind": "latest_article_publication",
                        "scored_observations": scored,
                    },
                    scope="所选时间窗的供应商新闻样本"
                    + (f"，标的{symbol}" if symbol else "，未指定ticker"),
                ),
            )

        return self._cached(
            [
                "news",
                provider,
                connection["id"],
                connection["revision"],
                symbol,
                lookback_hours,
                limit,
            ],
            1800,
            NewsSentimentResponse,
            load,
            refresh=_refresh,
        )

    def test_connection(self, connection_id, body):
        body = DataConnectionTestRequest.model_validate(body)
        symbol = _symbol(body.symbol)
        with self.database.session() as session:
            self._authorize(session, write=True)
            row = self._owned(session, connection_id)
            if row.revision != body.revision:
                raise AlternativeDataError("revision_conflict", "数据连接已更新，请刷新后重试", 409)
            provider = row.provider
        success, message = False, "所选端点检测失败，检查凭据、权限与额度；未自动购买订阅"
        try:
            if body.capability == "news" and provider == "massive":
                self.get_news_sentiment(connection_id=connection_id, limit=1, _refresh=True)
            elif body.capability == "options" and provider in {"massive", "alpaca"}:
                self.get_options(
                    symbol, provider=provider, connection_id=connection_id, _refresh=True
                )
            elif body.capability == "etf_flows" and provider == "intrinio":
                self.get_etf_flows(
                    [symbol],
                    provider=provider,
                    connection_id=connection_id,
                    lookback_days=7,
                    _refresh=True,
                )
            else:
                raise AlternativeDataError(
                    "capability_not_supported", "所选供应商不提供此能力", 422
                )
            success, message = True, "端点已响应；具体数据覆盖、缺失字段与时效需查看查询结果"
        except AlternativeDataError as error:
            message = error.message
        with self._lock, self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            row = self._owned(session, connection_id)
            if row.revision != body.revision:
                raise AlternativeDataError(
                    "revision_conflict", "检测期间数据连接已更新，请重新检测", 409
                )
            row.test_results = {
                **row.test_results,
                body.capability: "passed" if success else "failed",
            }
            row.tested_at, row.updated_at, row.revision = self.now(), self.now(), row.revision + 1
            session.flush()
            connection = self._view(row)
        return DataConnectionTestResponse(
            connection=connection, capability=body.capability, success=success, message=message
        )
