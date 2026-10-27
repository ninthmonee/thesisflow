"""Evidence-based daily market snapshots, on a bounded data-only execution pool."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from tradingagents.web.api.intelligence_schemas import (
    BenchmarkMetric,
    BreadthMetric,
    BriefContent,
    BriefGeneration,
    IntelligenceError,
    MarketBrief,
    MarketObservation,
    MarketSnapshot,
    MetricEvidence,
    OptionalMarketData,
    ProfessionalETFFlowPulse,
    ProfessionalMarketPulse,
    ProfessionalNewsPulse,
    ProfessionalOptionsPulse,
    ProfessionalPulseBaseline,
    ProfessionalPulseMeta,
    SectorMetric,
)

BENCHMARKS = (
    ("SPY", "标普500 ETF代理", "etf_proxy"),
    ("QQQ", "Nasdaq-100 ETF代理", "etf_proxy"),
    ("IWM", "小盘 ETF代理", "etf_proxy"),
    ("DIA", "道指 ETF代理", "etf_proxy"),
    ("^VIX", "VIX 预期波动指数", "volatility_index"),
)
SECTORS = (
    ("XLK", "信息技术"),
    ("XLF", "金融"),
    ("XLV", "医疗保健"),
    ("XLY", "可选消费"),
    ("XLP", "必需消费"),
    ("XLE", "能源"),
    ("XLI", "工业"),
    ("XLB", "材料"),
    ("XLU", "公用事业"),
    ("XLRE", "房地产"),
    ("XLC", "通信服务"),
)
OBSERVATION_SYMBOLS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOG",
    "META",
    "TSLA",
    "AVGO",
    "ORCL",
    "CRM",
    "ADBE",
    "AMD",
    "INTC",
    "QCOM",
    "MU",
    "JPM",
    "BAC",
    "GS",
    "MS",
    "V",
    "MA",
    "XOM",
    "CVX",
    "UNH",
    "JNJ",
    "LLY",
    "PFE",
    "WMT",
    "COST",
    "HD",
)
UNIVERSE_NAME = "美股观察样本30"
UNIVERSE_VERSION = "us-observation-30-v1-2026-10-24"


def _number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _month_before(value: date) -> date:
    month = value.month - 1 or 12
    year = value.year - (value.month == 1)
    return value.replace(
        year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])
    )


def _snapshot_id(snapshot: MarketSnapshot) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json", exclude={"id"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "mkt-" + hashlib.sha256(payload.encode()).hexdigest()[:32]


class MarketIntelligenceService:
    def __init__(
        self,
        market_data,
        cache_root: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_workers: int = 4,
        fetch_timeout_seconds: float = 15,
        cache_seconds: float = 900,
        brief_generator: Callable | None = None,
    ):
        if not 1 <= max_workers <= 8 or fetch_timeout_seconds <= 0 or cache_seconds <= 0:
            raise ValueError("invalid market aggregation limits")
        self.market_data = market_data
        self.cache_root = (
            Path(cache_root).expanduser().resolve() if cache_root is not None else None
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.fetch_timeout_seconds, self.cache_seconds = fetch_timeout_seconds, cache_seconds
        self.max_workers = max_workers
        self.brief_generator = brief_generator
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="market-data"
        )
        self._lock, self._build_lock = threading.RLock(), threading.Lock()
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._latest: dict[str, str] = {}
        self._responses: dict[str, dict] = {}
        self._responses_at: datetime | None = None
        self._pending = {}
        self._pending_options: tuple[int, float, float] | None = None
        self._invalidate_requested = False
        self._discard_pending = False

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("market aggregation clock must include timezone")
        return value.astimezone(UTC)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def configure(
        self, *, max_workers: int, fetch_timeout_seconds: float, cache_seconds: float
    ) -> bool:
        """Apply settings between batches, never multiply pools while a batch is running."""
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 8
            or fetch_timeout_seconds <= 0
            or cache_seconds <= 0
        ):
            raise ValueError("invalid market aggregation limits")
        with self._lock:
            self._pending_options = (max_workers, fetch_timeout_seconds, cache_seconds)
        if not self._build_lock.acquire(blocking=False):
            return False
        try:
            return self._apply_pending_options()
        finally:
            self._build_lock.release()

    def _apply_pending_options(self) -> bool:
        # Caller owns _build_lock. The public cache remains readable while batches finish.
        with self._lock:
            if self._invalidate_requested:
                self._responses, self._responses_at, self._latest = {}, None, {}
                self._discard_pending = bool(self._pending)
                self._invalidate_requested = False
            if self._pending_options is None:
                return True
            if any(not future.done() for future in self._pending):
                return False
            workers, timeout, ttl = self._pending_options
            if workers != self.max_workers:
                previous = self._executor
                self._executor = ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="market-data"
                )
                previous.shutdown(wait=False, cancel_futures=True)
            self.max_workers, self.fetch_timeout_seconds, self.cache_seconds = workers, timeout, ttl
            self._pending_options = None
            self._responses_at = None
            return True

    def invalidate_market_data_cache(self) -> bool:
        """Discard rebuildable freshness state; retain immutable snapshots used as evidence."""
        with self._lock:
            self._invalidate_requested = True
        if not self._build_lock.acquire(blocking=False):
            return False
        try:
            self._apply_pending_options()
            return True
        finally:
            self._build_lock.release()

    def _load(self, symbol: str) -> dict:
        result = self.market_data.get_bars(
            symbol,
            interval="1d",
            period="3mo",
            session="regular",
            adjustment="provider_native",
            limit=5000,
        )
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        return (
            dict(result) if isinstance(result, Mapping) and result.get("symbol") == symbol else {}
        )

    def _collect(self, *, refresh: bool) -> dict[str, dict]:
        now = self.now()
        # Never stack new batches behind requests still running from a timed-out batch.
        for future in list(self._pending):
            if future.done():
                symbol = self._pending.pop(future)
                if self._discard_pending:
                    continue
                try:
                    self._responses[symbol] = future.result()
                except Exception:
                    self._responses[symbol] = {}
        if self._pending:
            return self._responses
        self._discard_pending = False
        self._apply_pending_options()
        # Even explicit refresh is throttled; upstream services have their own cache/limits.
        expected_count = len(
            set(
                [row[0] for row in BENCHMARKS]
                + [row[0] for row in SECTORS]
                + list(OBSERVATION_SYMBOLS)
            )
        )
        incomplete = len(self._responses) < expected_count or any(
            not row or (row.get("meta") or {}).get("quality") in {"unavailable", "stale"}
            for row in self._responses.values()
        )
        interval = min(30, self.cache_seconds) if refresh or incomplete else self.cache_seconds
        if self._responses_at is not None and (now - self._responses_at).total_seconds() < interval:
            return self._responses
        symbols = tuple(
            dict.fromkeys(
                [row[0] for row in BENCHMARKS]
                + [row[0] for row in SECTORS]
                + list(OBSERVATION_SYMBOLS)
            )
        )
        futures = {self._executor.submit(self._load, symbol): symbol for symbol in symbols}
        finished, pending = wait(futures, timeout=self.fetch_timeout_seconds)
        responses = {}
        for future in finished:
            try:
                responses[futures[future]] = future.result()
            except Exception:
                responses[futures[future]] = {}
        for future in pending:
            if not future.cancel():
                self._pending[future] = futures[future]
        self._responses, self._responses_at = responses, now
        return responses

    @staticmethod
    def _data(
        response: dict, *, source: str | None = None, feed: str | None = None
    ) -> tuple[list[dict], str | None]:
        meta = response.get("meta") or {}
        if not isinstance(meta, Mapping) or meta.get("quality") == "unavailable":
            return [], "来源未提供可用日线"
        if (
            not isinstance(meta.get("source"), str)
            or not isinstance(meta.get("feed"), str)
            or not meta.get("source")
            or not meta.get("feed")
        ):
            return [], "数据缺少来源/feed，不能参与比较"
        if source is not None and (meta.get("source") != source or meta.get("feed") != feed):
            return [], "与基准来源/feed不一致，已排除比较"
        if (
            response.get("interval") != "1d"
            or response.get("session") != "regular"
            or response.get("adjustment") != "provider_native"
        ):
            return [], "周期、时段或调整口径不一致"
        by_date = {}
        for bar in response.get("bars") or []:
            if not isinstance(bar, Mapping) or bar.get("is_complete") is not True:
                continue
            try:
                stamp = date.fromisoformat(bar["date"]).isoformat()
            except (ValueError, TypeError, KeyError):
                continue
            close = _number(bar.get("close"), positive=True)
            if close is None:
                continue
            volume = _number(bar.get("volume"))
            by_date[stamp] = {
                "date": stamp,
                "close": close,
                "volume": volume if volume is not None and volume >= 0 else None,
            }
        rows = [by_date[key] for key in sorted(by_date)]
        return rows, None if rows else "没有完整交易日数据"

    @staticmethod
    def _window_dates(rows: list[dict], window: str) -> tuple[str | None, str | None]:
        if not rows:
            return None, None
        end = rows[-1]["date"]
        try:
            from exchange_calendars import get_calendar

            calendar = get_calendar("XNYS")
            sessions = [
                stamp.strftime("%Y-%m-%d")
                for stamp in calendar.sessions_in_range(
                    date.fromisoformat(end) - timedelta(days=65),
                    end,
                )
            ]
        except ImportError:
            raise IntelligenceError(
                "market_calendar_required", "市场日历组件尚未安装，不能计算交易日窗口", 503
            ) from None
        except (ValueError, KeyError):
            return None, end
        if end not in sessions:
            return None, end
        if window in {"1D", "5D"}:
            distance = 1 if window == "1D" else 5
            return (sessions[-distance - 1], end) if len(sessions) > distance else (None, end)
        threshold = _month_before(date.fromisoformat(end)).isoformat()
        candidates = [stamp for stamp in sessions if stamp <= threshold]
        return (candidates[-1], end) if candidates else (None, end)

    @staticmethod
    def _return(rows: list[dict], start: str | None, end: str | None) -> float | None:
        lookup = {row["date"]: row["close"] for row in rows}
        if start not in lookup or end not in lookup:
            return None
        return (lookup[end] / lookup[start] - 1) * 100

    def _build(self, responses: dict[str, dict], window: str) -> MarketSnapshot:
        spy_response = responses.get("SPY", {})
        spy_rows, spy_reason = self._data(spy_response)
        meta = spy_response.get("meta") or {}
        source, feed = meta.get("source"), meta.get("feed")
        start, end = self._window_dates(spy_rows, window)
        baseline_return = self._return(spy_rows, start, end)
        expected_history = []
        if end is not None and baseline_return is not None:
            from exchange_calendars import get_calendar

            expected_history = [
                stamp.strftime("%Y-%m-%d")
                for stamp in get_calendar("XNYS").sessions_in_range(
                    date.fromisoformat(end) - timedelta(days=65),
                    end,
                )
            ][-21:]
        warnings = []
        if spy_reason or baseline_return is None:
            warnings.append("SPY基准或窗口历史不足，相关比较不计算")
        warnings.append("聚合使用最近完整交易日，当前未完成日线不会混入；观察样本并非全市场")
        evidence: list[MetricEvidence] = []

        def add(
            identifier: str, label: str, value: float | None, unit: str, quality: str, method: str
        ):
            evidence.append(
                MetricEvidence(
                    id=identifier,
                    label=label,
                    value=value,
                    unit=unit,
                    as_of=end,
                    source=source,
                    feed=feed,
                    quality=quality,
                    method=method,
                )
            )
            return identifier

        def comparable(symbol: str):
            response = responses.get(symbol, {})
            rows, reason = self._data(response, source=source, feed=feed)
            coverage = (response.get("meta") or {}).get("coverage") or {}
            unverified_index = symbol.startswith("^") and coverage.get("calendar_verified") is False
            if unverified_index:
                today_et = self.now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
                rows = [row for row in rows if row["date"] < today_et]
            value = self._return(rows, start, end) if baseline_return is not None else None
            if value is None and reason is None:
                reason = (
                    "指数专属日历未验证，当前日期日线不参与完成日比较"
                    if unverified_index
                    else "缺少共同起止日数据，未进行跨日替代"
                )
            reported_quality = (response.get("meta") or {}).get("quality")
            quality = (
                "unavailable"
                if value is None
                else reported_quality
                if reported_quality in {"stale", "partial"}
                else "complete"
            )
            return rows, value, quality, reason

        benchmarks = []
        for symbol, name, kind in BENCHMARKS:
            rows, value, quality, reason = comparable(symbol)
            anchor = next((row for row in rows if row["date"] == end), None)
            price = anchor["close"] if anchor else None
            ids = [
                add(
                    f"benchmark:{symbol}:price",
                    name + "收盘值",
                    price,
                    "index" if kind == "volatility_index" else "USD",
                    quality,
                    "共同截止日的完整日线收盘值",
                ),
                add(
                    f"benchmark:{symbol}:return",
                    name + "窗口变化",
                    value,
                    "%",
                    quality,
                    "共同起止日价格变化百分比",
                ),
            ]
            benchmarks.append(
                BenchmarkMetric(
                    symbol=symbol,
                    name=name,
                    kind=kind,
                    price=price,
                    return_percent=round(value, 6) if value is not None else None,
                    as_of=end,
                    quality=quality,
                    reason=reason,
                    evidence_ids=ids,
                )
            )
        if ((responses.get("^VIX", {}).get("meta") or {}).get("coverage") or {}).get(
            "calendar_verified"
        ) is False:
            warnings.append(
                "VIX按来源日线观察，指数专属日历未验证；不把XNYS参考时段当作指数权威时段"
            )
        sectors = []
        for symbol, name in SECTORS:
            rows, value, quality, reason = comparable(symbol)
            eligible = [row for row in rows if end is not None and row["date"] <= end]
            anchor = next((row for row in eligible if row["date"] == end), None)
            volume = anchor["volume"] if anchor else None
            history_lookup = {row["date"]: row for row in eligible}
            prior_volumes = (
                [
                    history_lookup[stamp]["volume"]
                    for stamp in expected_history[:-1]
                    if stamp in history_lookup and history_lookup[stamp]["volume"] is not None
                ]
                if anchor
                else []
            )
            rvol = (
                volume / mean(prior_volumes)
                if volume is not None and len(prior_volumes) == 20 and mean(prior_volumes) > 0
                else None
            )
            relative = (
                value - baseline_return
                if value is not None and baseline_return is not None
                else None
            )
            turnover = (
                anchor["close"] * volume if anchor is not None and volume is not None else None
            )
            ids = [
                add(
                    f"sector:{symbol}:relative",
                    name + "相对SPY",
                    relative,
                    "pp",
                    quality,
                    "同窗口行业ETF价格收益率减SPY价格收益率",
                ),
                add(
                    f"sector:{symbol}:rvol",
                    name + "日级相对量",
                    rvol,
                    "倍",
                    quality if rvol is not None else "unavailable",
                    "完整日量除以前20个有效完整交易日平均量；相同feed",
                ),
                add(
                    f"sector:{symbol}:turnover",
                    name + "成交额估算",
                    turnover,
                    "USD",
                    quality if turnover is not None else "unavailable",
                    "完整日收盘价乘成交量，仅估算，不代表净流入",
                ),
            ]
            sectors.append(
                SectorMetric(
                    symbol=symbol,
                    name=name,
                    return_percent=round(value, 6) if value is not None else None,
                    relative_return_pp=round(relative, 6) if relative is not None else None,
                    volume=volume,
                    turnover_estimate_usd=round(turnover, 2) if turnover is not None else None,
                    daily_rvol=round(rvol, 6) if rvol is not None else None,
                    rvol_sample_count=len(prior_volumes),
                    as_of=end,
                    quality=quality,
                    reason=reason,
                    evidence_ids=ids,
                )
            )
        advancers = decliners = unchanged = 0
        above = []
        available = 0
        for symbol in OBSERVATION_SYMBOLS:
            rows, value, _quality, _reason = comparable(symbol)
            if value is None:
                continue
            available += 1
            if value > 0.000001:
                advancers += 1
            elif value < -0.000001:
                decliners += 1
            else:
                unchanged += 1
            history_lookup = {row["date"]: row["close"] for row in rows}
            history = [
                history_lookup[stamp] for stamp in expected_history[-20:] if stamp in history_lookup
            ]
            if len(history) == 20:
                above.append(history[-1] > mean(history))
        breadth_quality = (
            "complete"
            if available == len(OBSERVATION_SYMBOLS)
            else ("partial" if available else "unavailable")
        )
        breadth_ids = [
            add(
                "breadth:advancers",
                "观察样本上涨家数",
                float(advancers),
                "家",
                breadth_quality,
                "同窗口共同起止日，固定观察样本等权计数",
            ),
            add(
                "breadth:decliners",
                "观察样本下跌家数",
                float(decliners),
                "家",
                breadth_quality,
                "同窗口共同起止日，缺失样本不计入",
            ),
            add(
                "breadth:coverage",
                "观察样本有效覆盖",
                float(available),
                "家",
                breadth_quality,
                "分母为明确的30只观察股票",
            ),
            add(
                "breadth:sma20",
                "观察样本站上20日均线比例",
                mean(above) * 100 if above else None,
                "%",
                breadth_quality if above else "unavailable",
                "有效20个完整交易日样本；分母单独显示",
            ),
        ]
        breadth = BreadthMetric(
            universe_name=UNIVERSE_NAME,
            universe_version=UNIVERSE_VERSION,
            universe_symbols=list(OBSERVATION_SYMBOLS),
            requested_count=len(OBSERVATION_SYMBOLS),
            available_count=available,
            advancers=advancers if available else None,
            decliners=decliners if available else None,
            unchanged=unchanged if available else None,
            above_sma20_percent=round(mean(above) * 100, 4) if above else None,
            sma20_sample_count=len(above),
            quality=breadth_quality,
            evidence_ids=breadth_ids,
        )
        observations = []
        if baseline_return is not None and baseline_return > 0 and decliners > advancers:
            observations.append(
                MarketObservation(
                    text="SPY上涨，但观察样本下跌家数更多；样本广度与基准方向存在分歧",
                    evidence_ids=["benchmark:SPY:return", "breadth:advancers", "breadth:decliners"],
                )
            )
        if available != len(OBSERVATION_SYMBOLS):
            warnings.append(
                f"观察样本仅有{available}/{len(OBSERVATION_SYMBOLS)}只可比数据，缺失不补零"
            )
        missing_sectors = sum(row.relative_return_pp is None for row in sectors)
        if missing_sectors:
            warnings.append(f"{missing_sectors}个行业代理缺少可比数据")
        insufficient_history = (
            any(row.daily_rvol is None for row in sectors) or len(above) < available
        )
        if insufficient_history:
            warnings.append("部分样本历史不足或成交量缺失，未计算对应日级RVOL/20日均线比例")
        stale = any(
            (response.get("meta") or {}).get("quality") == "stale"
            for response in responses.values()
        )
        upstream_partial = any(
            (response.get("meta") or {}).get("quality") == "partial"
            for response in responses.values()
        )
        if upstream_partial:
            warnings.append("部分上游数据标记不完整，聚合保留该质量限制")
        quality = (
            "unavailable"
            if baseline_return is None
            else "stale"
            if stale
            else "partial"
            if upstream_partial
            or missing_sectors
            or insufficient_history
            or available < len(OBSERVATION_SYMBOLS)
            or any(row.quality == "unavailable" for row in benchmarks)
            else "complete"
        )
        if stale:
            warnings.append("部分来源返回过期缓存，数值按其实际交易日显示")
        snapshot = MarketSnapshot(
            id="mkt-" + "0" * 32,
            window=window,
            window_start=start,
            window_end=end,
            as_of=end,
            received_at=self.now(),
            source=source,
            feed=feed,
            quality=quality,
            benchmarks=benchmarks,
            sectors=sectors,
            breadth=breadth,
            evidence=evidence,
            observed_divergences=observations,
            optional_data=[
                OptionalMarketData(
                    id="etf_net_flows",
                    name="ETF净申赎",
                    reason="此价量快照未包含ETF份额流维度；请在专业维度选择ETF后显式加载估算或已有账号的报告数据。成交额不是净流入。",
                ),
                OptionalMarketData(
                    id="options_put_call",
                    name="期权结构",
                    reason="此价量快照未包含期权维度；请在专业维度选择标的后显式加载，按来源注明合约覆盖、持仓与延迟。",
                ),
                OptionalMarketData(
                    id="market_news_sentiment",
                    name="全市场新闻情绪",
                    reason="此价量快照未包含新闻情绪；请在专业维度明确启用后加载供应商新闻样本。是否配置账号由该维度判断，不代表全媒体覆盖。",
                ),
            ],
            warnings=warnings,
        )
        return snapshot.model_copy(update={"id": _snapshot_id(snapshot)})

    def _remember(self, snapshot: MarketSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.id] = snapshot.model_copy(deep=True)
            self._latest[snapshot.window] = snapshot.id
            if len(self._snapshots) > 32:
                oldest = min(self._snapshots, key=lambda key: self._snapshots[key].received_at)
                self._snapshots.pop(oldest)
        if self.cache_root is not None:
            self.cache_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            path = self.cache_root / f"{snapshot.id}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)

    def get_snapshot(self, window: str = "1D", *, refresh: bool = False) -> MarketSnapshot:
        if window not in {"1D", "5D", "1M"}:
            raise IntelligenceError("invalid_market_window", "市场窗口须为1D、5D或1M", 422)
        with self._lock:
            existing = self._snapshots.get(self._latest.get(window, ""))
            ttl = (
                min(30, self.cache_seconds)
                if existing is not None and existing.quality in {"partial", "unavailable"}
                else self.cache_seconds
            )
            if (
                existing is not None
                and not refresh
                and not self._invalidate_requested
                and self._pending_options is None
                and (self.now() - existing.received_at).total_seconds() < ttl
            ):
                return existing.model_copy(deep=True)
        if not self._build_lock.acquire(blocking=False):
            if existing is not None:
                return existing.model_copy(deep=True)
            raise IntelligenceError("market_snapshot_building", "市场快照正在刷新，请稍后重试", 503)
        try:
            self._apply_pending_options()
            responses = self._collect(refresh=refresh)
            snapshot = self._build(responses, window)
            self._remember(snapshot)
            return snapshot.model_copy(deep=True)
        finally:
            self._apply_pending_options()
            self._build_lock.release()

    def get_cached_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        if not re.fullmatch(r"mkt-[0-9a-f]{32}", snapshot_id):
            raise IntelligenceError("invalid_snapshot_id", "市场快照编号无效", 422)
        with self._lock:
            if snapshot_id in self._snapshots:
                return self._snapshots[snapshot_id].model_copy(deep=True)
        if self.cache_root is not None:
            path = self.cache_root / f"{snapshot_id}.json"
            try:
                if path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError
                snapshot = MarketSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
                if snapshot.id != snapshot_id or _snapshot_id(snapshot) != snapshot_id:
                    raise ValueError
                with self._lock:
                    self._snapshots[snapshot_id] = snapshot
                return snapshot.model_copy(deep=True)
            except (OSError, ValueError, ValidationError):
                pass
        raise IntelligenceError(
            "market_snapshot_not_found", "市场快照不存在或校验失败，请重新加载市场", 404
        )

    def generate_brief(
        self, snapshot_id: str, language: str = "Chinese", *, generator=None
    ) -> MarketBrief:
        snapshot = self.get_cached_snapshot(snapshot_id)
        if snapshot.as_of is None or snapshot.quality == "unavailable":
            raise IntelligenceError(
                "market_data_insufficient", "市场数据不足，暂不能生成有依据的简报"
            )
        callback = generator or self.brief_generator
        if callback is None:
            raise IntelligenceError("market_model_required", "请先配置市场简报使用的模型")
        if language not in {"Chinese", "English"}:
            raise IntelligenceError("invalid_brief_language", "报告语言无效", 422)
        try:
            result = callback(snapshot.model_dump(mode="json"), language)
            if isinstance(result, BriefGeneration):
                generated = result
            elif isinstance(result, Mapping) and "content" in result:
                generated = BriefGeneration.model_validate(result)
            else:
                generated = BriefGeneration(content=BriefContent.model_validate(result))
        except IntelligenceError:
            raise
        except ValidationError:
            raise IntelligenceError(
                "invalid_market_brief", "简报结构未通过校验，未作为有效报告发布", 422
            ) from None
        except Exception:
            raise IntelligenceError(
                "market_brief_failed", "模型未能完成市场简报，请检查连接后重试", 503
            ) from None
        known_ids = {row.id for row in snapshot.evidence}
        claims = [
            generated.content.state,
            *generated.content.changes,
            *generated.content.attention,
            *generated.content.divergences,
            *generated.content.risks,
        ]
        if any(
            identifier not in known_ids for claim in claims for identifier in claim.evidence_ids
        ):
            raise IntelligenceError(
                "invalid_brief_evidence", "简报引用了快照之外的证据，未作为有效报告发布", 422
            )
        return MarketBrief(
            id=f"brief-{uuid4()}",
            snapshot_id=snapshot.id,
            as_of=snapshot.as_of,
            generated_at=self.now(),
            model=generated.model,
            provider=generated.provider,
            content=generated.content,
            observed_divergences=snapshot.observed_divergences,
            data_gaps=[*snapshot.warnings, *[f"{row.name}：{row.reason}" for row in snapshot.optional_data]],
            scope_notice=snapshot.breadth.scope_notice,
        )


class ProfessionalMarketPulseService:
    """Opt-in professional dimensions, with bounded same-scope local baselines."""

    def __init__(self, alternatives, cache_root, *, clock=None, timeout_seconds=20):
        if not 0 < timeout_seconds <= 30:
            raise ValueError("professional pulse timeout must be 0–30 seconds")
        self.alternatives = alternatives
        self.clock = clock or (lambda: datetime.now(UTC))
        scope = hashlib.sha256(
            json.dumps([alternatives.workspace_id, alternatives.owner_id]).encode()
        ).hexdigest()[:24]
        self.root = Path(cache_root).expanduser().resolve() / "professional-pulse" / scope
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="professional-pulse")
        self._pending = {}

    def now(self):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("professional pulse clock must include timezone")
        return now.astimezone(UTC)

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _hash(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    @staticmethod
    def _symbols(values, maximum):
        if not isinstance(values, (list, tuple)) or len(values) > maximum:
            raise IntelligenceError(
                "invalid_professional_sample", "期权最多5个、ETF最多10个，须显式选择美股代码", 422
            )
        result = []
        for value in values:
            if not isinstance(value, str):
                raise IntelligenceError("invalid_professional_sample", "样本代码无效", 422)
            symbol = value.strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9-]*(?:\.[AB])?", symbol) or symbol.endswith(
                ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")
            ):
                raise IntelligenceError(
                    "invalid_professional_sample", "专业市场维度仅支持美国上市股票/ETF代码", 422
                )
            if symbol not in result:
                result.append(symbol)
        return result

    def _connection(self, rows, provider, connection_id):
        if provider in {"yahoo", "ishares"}:
            return None, True
        candidates = [
            row
            for row in rows
            if row.provider == provider and (not connection_id or row.id == connection_id)
        ]
        if not candidates:
            return None, False
        if connection_id:
            selected = candidates[0]
        else:
            enabled = [row for row in candidates if row.enabled]
            selected = max(enabled, key=lambda row: row.created_at) if enabled else candidates[-1]
        configured = (
            selected.enabled
            and selected.credential_configured
            and (provider != "alpaca" or selected.secondary_credential_configured)
        )
        return selected, configured

    def _baseline(self, signature, raw_meta, metrics, *, extra=None):
        keys = dict.fromkeys(metrics)
        unknown = ProfessionalPulseBaseline(
            deltas=keys, reason="尚无同来源、feed、窗口和样本且不同数据时点的本机历史观察"
        )
        as_of = raw_meta.get("as_of")
        if (
            not isinstance(as_of, str)
            or not as_of
            or raw_meta.get("quality") not in {"complete", "partial"}
        ):
            unknown.reason = "数据时点未知、过期或不可用，不能建立同口径基线"
            return unknown
        key = self._hash([signature, raw_meta.get("source"), raw_meta.get("feed"), extra])
        path = self.root / f"{key}.json"
        if self.root.resolve() != self.root:
            unknown.reason = "基线缓存路径无效，保留未知"
            return unknown
        now, history = self.now(), []
        with self._lock:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 64_000:
                try:
                    loaded = json.loads(path.read_text())
                    for row in loaded if isinstance(loaded, list) else []:
                        if (
                            not isinstance(row, dict)
                            or not isinstance(row.get("as_of"), str)
                            or not isinstance(row.get("metrics"), dict)
                        ):
                            continue
                        stamp = datetime.fromisoformat(row["observed_at"])
                        if (
                            stamp.tzinfo is not None
                            and 0 <= (now - stamp).total_seconds() <= 35 * 86400
                        ):
                            history.append(row)
                except (OSError, ValueError, KeyError, TypeError):
                    unknown.reason = "此前本机基线缓存不可读，当前保留未知"
            eligible = [
                row
                for row in history
                if row.get("as_of")
                and row["as_of"] != as_of
                and row["as_of"] < as_of
                and isinstance(row.get("metrics"), dict)
            ]
            if eligible:
                previous = max(eligible, key=lambda row: row["as_of"])
                deltas = {}
                for name, value in metrics.items():
                    old = _number(previous["metrics"].get(name))
                    delta = value - old if value is not None and old is not None else None
                    deltas[name] = _number(delta)
                if any(value is not None for value in deltas.values()):
                    unknown = ProfessionalPulseBaseline(
                        status="available",
                        as_of=previous["as_of"],
                        observed_at=previous["observed_at"],
                        deltas=deltas,
                        reason="同口径两次本机观察的标量差；滚动窗口可重叠，不能解释为交易胜率或全市场情绪变化",
                    )
            history = [row for row in history if row.get("as_of") != as_of]
            history.append({"as_of": as_of, "observed_at": now.isoformat(), "metrics": metrics})
            encoded = json.dumps(history[-8:], allow_nan=False).encode()
            try:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
                if self.root.is_symlink() or path.is_symlink() or len(encoded) > 64_000:
                    raise OSError("invalid baseline cache")
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".pulse-", suffix=".tmp", dir=self.root
                )
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        os.fchmod(output.fileno(), 0o600)
                        output.write(encoded)
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                files = sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime)
                for item in files[:-128]:
                    item.unlink(missing_ok=True)
            except OSError:
                unknown.reason += "；当前基线未能持久保存"
        return unknown

    def _meta(
        self,
        raw,
        signature,
        metrics,
        *,
        provider,
        method,
        availability="available",
        extra=None,
        warnings=(),
    ):
        raw = raw or {}
        if availability == "available" and raw.get("quality", "unavailable") == "unavailable":
            availability = "unavailable"
        return ProfessionalPulseMeta(
            availability=availability,
            source=raw.get("source", provider),
            feed=raw.get("feed"),
            as_of=raw.get("as_of"),
            received_at=raw.get("received_at", self.now()),
            quality=raw.get("quality", "unavailable"),
            coverage=raw.get("coverage", {}),
            scope=raw.get("scope", "仅显式选择的样本维度，当前无可用数据"),
            method=method,
            warnings=[*raw.get("warnings", []), *warnings],
            baseline=self._baseline(signature, raw, metrics, extra=extra)
            if availability == "available"
            else ProfessionalPulseBaseline(
                deltas=dict.fromkeys(metrics), reason="当前维度没有可用数据，基线未知"
            ),
        )

    @staticmethod
    def _availability(items):
        values = [row.meta.availability for row in items]
        if not values:
            return "disabled"
        if "available" in values:
            return "available"
        if all(value == "configuration_required" for value in values):
            return "configuration_required"
        return "error" if "error" in values else "unavailable"

    def _submit(self, key, operation, refresh):
        with self._lock:
            now = self.now()
            for old, (stamp, future) in list(self._pending.items()):
                if future.done() and (now - stamp).total_seconds() > 30:
                    del self._pending[old]
            pending = self._pending.get(key)
            if pending and (not pending[1].done() or not refresh):
                return pending[1]
            if len(self._pending) >= 16:
                return None
            future = self._executor.submit(operation)
            self._pending[key] = now, future
            return future

    def get_pulse(
        self,
        options_symbols=(),
        etf_symbols=(),
        news_symbol=None,
        include_news=False,
        options_provider="yahoo",
        flows_provider="ishares",
        news_provider="massive",
        options_connection_id=None,
        flows_connection_id=None,
        news_connection_id=None,
        lookback_days=30,
        lookback_hours=24,
        refresh=False,
    ):
        if (
            options_provider not in {"yahoo", "massive", "alpaca"}
            or flows_provider not in {"ishares", "intrinio"}
            or news_provider != "massive"
            or not isinstance(include_news, bool)
            or not isinstance(refresh, bool)
            or isinstance(lookback_days, bool)
            or isinstance(lookback_hours, bool)
            or not 1 <= lookback_days <= 90
            or not 1 <= lookback_hours <= 168
        ):
            raise IntelligenceError("invalid_professional_request", "专业市场样本参数无效", 422)
        options_symbols, etf_symbols = (
            self._symbols(options_symbols, 5),
            self._symbols(etf_symbols, 10),
        )
        news_symbol = self._symbols([news_symbol], 1)[0] if news_symbol else None
        # Public connection metadata also authorizes every read, including an all-disabled pulse.
        connections = self.alternatives.list_connections()
        option_connection, options_ready = self._connection(
            connections, options_provider, options_connection_id
        )
        flow_connection, flows_ready = self._connection(
            connections, flows_provider, flows_connection_id
        )
        news_connection, news_ready = self._connection(
            connections, news_provider, news_connection_id
        )
        identities = [
            (row.id, row.revision, row.feed) if row else None
            for row in (option_connection, flow_connection, news_connection)
        ]
        signature = self._hash(
            [
                sorted(options_symbols),
                sorted(etf_symbols),
                news_symbol,
                include_news,
                options_provider,
                flows_provider,
                news_provider,
                lookback_days,
                lookback_hours,
                identities,
            ]
        )
        tasks = []
        for symbol in options_symbols:
            key = (signature, "options", symbol)
            future = (
                self._submit(
                    key,
                    lambda symbol=symbol: self.alternatives.get_options(
                        symbol,
                        provider=options_provider,
                        connection_id=option_connection.id if option_connection else None,
                        max_expirations=1,
                        _refresh=refresh,
                    ),
                    refresh,
                )
                if options_ready
                else None
            )
            tasks.append(("options", symbol, options_provider, options_ready, future))
        for symbol in etf_symbols:
            key = (signature, "flows", symbol)
            future = (
                self._submit(
                    key,
                    lambda symbol=symbol: self.alternatives.get_etf_flows(
                        [symbol],
                        provider=flows_provider,
                        connection_id=flow_connection.id if flow_connection else None,
                        lookback_days=lookback_days,
                        _refresh=refresh,
                    ),
                    refresh,
                )
                if flows_ready
                else None
            )
            tasks.append(("flows", symbol, flows_provider, flows_ready, future))
        if include_news:
            future = (
                self._submit(
                    (signature, "news", news_symbol),
                    lambda: self.alternatives.get_news_sentiment(
                        provider=news_provider,
                        connection_id=news_connection.id if news_connection else None,
                        symbol=news_symbol,
                        lookback_hours=lookback_hours,
                        limit=100,
                        _refresh=refresh,
                    ),
                    refresh,
                )
                if news_ready
                else None
            )
            tasks.append(("news", news_symbol, news_provider, news_ready, future))
        wait([task[-1] for task in tasks if task[-1] is not None], timeout=self.timeout_seconds)
        options, flows, news, warnings = [], [], None, []
        for kind, symbol, provider, ready, future in tasks:
            availability = "configuration_required" if not ready else "unavailable"
            raw, messages = {}, []
            if not ready:
                messages.append("所选账号未启用或凭据未配置；未调用该来源，可到数据连接设置处理")
            elif future is None or not future.done():
                messages.append("来源读取尚未完成或本机请求队列已满，本次不补造数据")
            else:
                try:
                    value = future.result()
                    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                    if not isinstance(raw, dict):
                        raise ValueError("invalid professional response")
                    availability = "available"
                except Exception:
                    availability = "error"
                    messages.append(
                        "来源未能返回有效结果，请查看数据连接和对应专业数据页；未回显上游异常"
                    )
            scalar = {}
            if kind == "options":
                contracts = raw.get("contracts", [])
                scalar = {
                    "put_call_volume_ratio": _number(raw.get("put_call_volume_ratio")),
                    "put_call_oi_ratio": _number(raw.get("put_call_oi_ratio")),
                    "contract_count": len(contracts) if raw else None,
                }
                extra = self._hash(sorted(row.get("symbol", "") for row in contracts))
                meta = self._meta(
                    raw.get("meta"),
                    [signature, kind, symbol],
                    scalar,
                    provider=provider,
                    method="仅返回的同一到期日及合约样本put/call成交量与持仓量比；不是全市场比率，也不是净买入",
                    availability=availability,
                    extra=extra,
                    warnings=messages,
                )
                options.append(
                    ProfessionalOptionsPulse(
                        symbol=symbol,
                        contract_count=len(contracts) if raw else None,
                        expiration_count=len(raw.get("selected_expirations", [])) if raw else None,
                        put_call_volume_ratio=scalar["put_call_volume_ratio"],
                        put_call_oi_ratio=scalar["put_call_oi_ratio"],
                        meta=meta,
                    )
                )
            elif kind == "flows":
                fund = next(
                    (row for row in raw.get("funds", []) if row.get("symbol") == symbol), {}
                )
                points = fund.get("points", [])
                values = [_number(row.get("net_flow_usd")) for row in points]
                valid = [value for value in values if value is not None]
                scalar = {
                    "latest_flow_usd": values[-1] if values else None,
                    "available_points_sum_usd": _number(sum(valid)) if valid else None,
                }
                kinds = {row.get("flow_kind") for row in points}
                flow_kind = (
                    next(iter(kinds))
                    if len(kinds) == 1 and kinds <= {"estimated", "vendor_reported"}
                    else "mixed"
                    if kinds
                    else "unknown"
                )
                meta = self._meta(
                    fund.get("meta"),
                    [signature, kind, symbol],
                    scalar,
                    provider=provider,
                    method="只对返回且非缺失的日级份额流求和；估算/报告口径单列，不代表该主题或机构净买入",
                    availability=availability,
                    extra=flow_kind,
                    warnings=[*messages, "滚动窗口合计可重叠，差值不等于两次观察之间新流入的金额"],
                )
                flows.append(
                    ProfessionalETFFlowPulse(
                        symbol=symbol,
                        flow_kind=flow_kind,
                        point_count=len(points) if fund else None,
                        valid_point_count=len(valid) if fund else None,
                        window_start=points[0].get("as_of_date") if points else None,
                        window_end=points[-1].get("as_of_date") if points else None,
                        **scalar,
                        meta=meta,
                    )
                )
            else:
                scalar = {
                    name: _number(raw.get(name))
                    for name in (
                        "sentiment_balance",
                        "article_count",
                        "positive",
                        "negative",
                        "neutral",
                    )
                }
                meta = self._meta(
                    raw.get("meta"),
                    [signature, kind, symbol],
                    scalar,
                    provider=provider,
                    method="供应商可获新闻按ticker情绪观察数计数；正负余额=(正−负)/已分类；缺失不当中性",
                    availability=availability,
                    warnings=[
                        *messages,
                        "未指定ticker仅表示供应商广泛新闻样本，不代表美股全公司或全媒体覆盖",
                    ],
                )
                news = ProfessionalNewsPulse(
                    symbol=symbol,
                    **{
                        name: raw.get(name)
                        for name in (
                            "article_count",
                            "publisher_count",
                            "ticker_count",
                            "positive",
                            "negative",
                            "neutral",
                            "unclassified_articles",
                        )
                    },
                    sentiment_balance=_number(raw.get("sentiment_balance")),
                    meta=meta,
                )
        received_at = self.now()
        return ProfessionalMarketPulse(
            id="pro-" + self._hash([signature, received_at.isoformat()])[:32],
            received_at=received_at,
            sample_signature=signature,
            options_availability=self._availability(options),
            flows_availability=self._availability(flows),
            news_availability=news.meta.availability if news else "disabled",
            options=options,
            etf_flows=flows,
            news=news,
            warnings=warnings,
        )
