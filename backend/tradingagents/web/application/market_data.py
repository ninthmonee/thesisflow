"""Independent numeric Yahoo data, calendar-aware bars and atomic Parquet caches."""

from __future__ import annotations

import calendar as month_calendar
import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.dataflows.vendors.web_market import YahooHistoryProvider
from tradingagents.web.api.market_schemas import MarketDataError
from tradingagents.web.domain.technical_methods import WILDER_METHOD, wilder_smoothing

ET = ZoneInfo("America/New_York")
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")
PERIODS = ("5d", "1mo", "3mo", "6mo", "1y", "2y", "5y")
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
ADJUSTMENTS = {
    "provider_native": "Yahoo 原生历史价格口径，可能包含拆股调整；不等同未复权逐笔价格",
    "dividend_adjusted": "Yahoo 自动分红/拆股调整口径；用于回顾比较，非当时可得原始价格",
}


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_\-]{0,31}", symbol):
        raise MarketDataError("invalid_symbol", "证券代码格式无效", 422)
    # Yahoo exchange suffixes imply another market/currency. Only A/B share
    # class dots are accepted; FX/futures identifiers require separate semantics.
    if (
        ("." in symbol and not re.fullmatch(r"[A-Z0-9]+\.[AB]", symbol))
        or "=" in symbol
        or symbol.endswith(("-USD", "-USDT", "-USDC"))
    ):
        raise MarketDataError(
            "market_not_supported",
            "此行情工作台当前仅支持美股、美国上市ADR、ETF与指数；不支持海外市场后缀或币种转换",
            422,
        )
    return symbol


class MarketDataService:
    def __init__(self, cache_root: str | Path, *, provider=None,
                 clock: Callable[[], datetime] | None = None, cache_ttl_seconds: float | None = None,
                 minute_cache_ttl_seconds: float = 30, daily_cache_ttl_seconds: float = 180):
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.provider = provider if provider is not None else YahooHistoryProvider(self.cache_root)
        self.source = getattr(self.provider, "source", "custom")
        self.feed = getattr(self.provider, "feed", "custom_history")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache_ttl_seconds = cache_ttl_seconds
        self.minute_cache_ttl_seconds = minute_cache_ttl_seconds
        self.daily_cache_ttl_seconds = daily_cache_ttl_seconds
        self._locks: dict[str, RLock] = {}
        self._lock = RLock()
        self._calendar = None

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("market clock must be timezone-aware")
        return now.astimezone(UTC)

    def _exchange(self):
        with self._lock:
            if self._calendar is None:
                import exchange_calendars
                self._calendar = exchange_calendars.get_calendar("XNYS")
            return self._calendar

    def capabilities(self) -> dict:
        return {"source": self.source, "feed": self.feed, "intervals": list(INTERVALS),
                "periods": list(PERIODS), "interval_periods": {
                    interval: [period for period in PERIODS if self._valid_pair(interval, period)]
                    for interval in INTERVALS}, "sessions": ["regular", "extended"],
                "adjustments": ADJUSTMENTS, "quote_kind": "bar_close",
                "realtime_guaranteed": False, "nbbo": False,
                "warning": "价格来自供应商 K 线收盘字段，不是逐笔、NBBO 或有实时 SLA 的报价。"}

    @staticmethod
    def _valid_pair(interval: str, period: str) -> bool:
        if interval == "1m":
            return period == "5d"
        if interval in {"5m", "15m", "30m"}:
            return period in {"5d", "1mo"}
        if interval == "1h":
            return period in {"5d", "1mo", "3mo", "6mo", "1y"}
        return True

    def _validate(self, symbol, interval, period, session, adjustment, limit):
        symbol = _symbol(symbol)
        if interval not in INTERVALS or period not in PERIODS or not self._valid_pair(interval, period):
            raise MarketDataError("unsupported_window", "周期超出来源历史范围，请缩短分钟窗口。", 422)
        if session not in {"regular", "extended"} or adjustment not in ADJUSTMENTS:
            raise MarketDataError("invalid_market_options", "时段或调整口径无效", 422)
        if interval not in MINUTES and session == "extended":
            raise MarketDataError("unsupported_session", "盘前盘后仅适用于分钟/小时数据", 422)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise MarketDataError("invalid_limit", "行情数量须为 1 至 5000", 422)
        return symbol

    def _cache_path(self, symbol, interval, period, session, adjustment):
        key = json.dumps(["market-v2", self.source, self.feed, symbol, interval, period, session, adjustment])
        return self.cache_root / "bars" / f"{hashlib.sha256(key.encode()).hexdigest()}.parquet"

    @staticmethod
    def _read_cache(path: Path) -> tuple[list[dict], dict] | None:
        if not path.is_file() or path.is_symlink():
            return None
        try:
            import pyarrow.parquet as pq
            table = pq.read_table(path)
            metadata = json.loads((table.schema.metadata or {})[b"tradingagents.market"])
            received_at = datetime.fromisoformat(metadata["received_at"])
            if received_at.tzinfo is None or not isinstance(metadata.get("coverage"), dict):
                return None
            rows = table.to_pylist()
            return (rows, metadata) if rows else None
        except (OSError, ValueError, KeyError):
            return None

    @staticmethod
    def _write_cache(path: Path, bars: list[dict], metadata: dict) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f"{path.name}.tmp-{uuid4()}")
        try:
            table = pa.Table.from_pylist(bars).replace_schema_metadata({
                b"tradingagents.market": json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode()})
            pq.write_table(table, temporary)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _day_bounds(self, day: date):
        exchange = self._exchange()
        label = pd.Timestamp(day)
        if not exchange.is_session(label):
            return None
        return exchange.session_open(label).to_pydatetime(), exchange.session_close(label).to_pydatetime()

    def _period_close(self, day: date, interval: str):
        if interval == "1wk":
            start = day - timedelta(days=day.weekday())
            end = start + timedelta(days=6)
        elif interval == "1mo":
            start = day.replace(day=1)
            end = day.replace(day=month_calendar.monthrange(day.year, day.month)[1])
        else:
            bounds = self._day_bounds(day)
            return bounds[1] if bounds else None
        sessions = self._exchange().sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        return self._exchange().session_close(sessions[-1]).to_pydatetime() if len(sessions) else None

    def _normalize(self, frame: pd.DataFrame, interval: str, session: str, now: datetime):
        records = {}
        dropped = duplicates = 0
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return [], 0, 0
        timestamp_column = next((name for name in ("timestamp", "Datetime", "Date") if name in frame.columns), None)
        for index, row in frame.iterrows():
            raw_timestamp = row[timestamp_column] if timestamp_column else index
            if isinstance(raw_timestamp, (int, float)):
                dropped += 1
                continue
            try:
                stamp = pd.Timestamp(raw_timestamp)
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize(ET, ambiguous="NaT", nonexistent="NaT")
                if pd.isna(stamp):
                    raise ValueError
                stamp = stamp.tz_convert("UTC")
                local = stamp.tz_convert(ET)
                day = local.date()
                if stamp.to_pydatetime() > now:
                    raise ValueError
                bounds = self._day_bounds(day)
                if interval in MINUTES:
                    if not bounds:
                        raise ValueError
                    opening, closing = bounds
                    tag = "pre" if stamp.to_pydatetime() < opening else "post" if stamp.to_pydatetime() >= closing else "regular"
                    if session == "regular" and tag != "regular":
                        continue
                    if not time(4) <= local.time() < time(20):
                        continue
                    ending = stamp.to_pydatetime() + timedelta(minutes=MINUTES[interval])
                    if tag == "regular":
                        ending = min(ending, closing)
                else:
                    tag = "regular"
                    ending = self._period_close(day, interval)
                    if ending is None:
                        raise ValueError
                point = {"timestamp": stamp.isoformat(), "date": day.isoformat(),
                         "session": tag, "is_complete": ending <= now}
                for key, column in (("open", "Open"), ("high", "High"), ("low", "Low"),
                                    ("close", "Close"), ("volume", "Volume")):
                    value = _number(row.get(column, row.get(key)))
                    point[key] = value if value is not None and (value >= 0 if key == "volume" else value > 0) else None
                high, low = point["high"], point["low"]
                known_prices = [point[key] for key in ("open", "close") if point[key] is not None]
                invalid_range = (
                    high is not None and low is not None and high < low
                ) or any((high is not None and value > high) or (low is not None and value < low)
                         for value in known_prices)
                if invalid_range:
                    # The source does not identify the incorrect field; do not infer a price.
                    for key in ("open", "high", "low", "close"):
                        point[key] = None
                if point["timestamp"] in records:
                    duplicates += 1
                records[point["timestamp"]] = point
            except (ValueError, TypeError, OverflowError):
                dropped += 1
        return sorted(records.values(), key=lambda point: point["timestamp"]), dropped, duplicates

    def get_bars(self, symbol: str, *, interval="1d", period="1mo", session="regular",
                 adjustment="provider_native", limit=5000) -> dict:
        symbol = self._validate(symbol, interval, period, session, adjustment, limit)
        path = self._cache_path(symbol, interval, period, session, adjustment)
        with self._lock:
            lock = self._locks.setdefault(str(path), RLock())
        with lock:
            now = self._now()
            cached = self._read_cache(path)
            ttl = self.cache_ttl_seconds if self.cache_ttl_seconds is not None else (
                self.minute_cache_ttl_seconds if interval in MINUTES else self.daily_cache_ttl_seconds)
            cache_state = "fresh"
            if cached and 0 <= (now - datetime.fromisoformat(cached[1]["received_at"])).total_seconds() < ttl:
                bars, metadata = cached
                metadata = deepcopy(metadata)
                cache_state = "hit"
            else:
                try:
                    frame = self.provider.history(symbol, period=period, interval=interval,
                                                  prepost=session == "extended",
                                                  auto_adjust=adjustment == "dividend_adjusted", timeout=15)
                    now = self._now()
                    bars, dropped, duplicates = self._normalize(frame, interval, session, now)
                    if not bars:
                        raise MarketDataError("market_data_unavailable", "来源未返回可用行情", 503)
                    incomplete = any(not bar["is_complete"] for bar in bars)
                    missing = any(any(bar[key] is None for key in ("open", "high", "low", "close", "volume")) for bar in bars)
                    warnings = []
                    if incomplete:
                        warnings.append("包含未完结 K 线")
                    if missing:
                        warnings.append("部分原始字段缺失或无效，未填补为成交或价格")
                    if dropped or duplicates:
                        warnings.append("已剔除无效时间并合并重复柱")
                    covered_sessions = self._exchange().sessions_in_range(
                        pd.Timestamp(bars[0]["date"]), pd.Timestamp(bars[-1]["date"]))
                    expected_regular = None
                    available_regular = sum(bar["session"] == "regular" for bar in bars)
                    if interval in MINUTES:
                        expected_regular = 0
                        for label in covered_sessions:
                            opening = self._exchange().session_open(label).to_pydatetime()
                            closing = min(self._exchange().session_close(label).to_pydatetime(), now)
                            expected_regular += max(0, math.ceil((closing - opening).total_seconds() / (MINUTES[interval] * 60)))
                    elif interval == "1d":
                        expected_regular = len(covered_sessions)
                    gaps = expected_regular is not None and available_regular < expected_regular
                    if gaps:
                        warnings.append("常规时段存在缺柱或来源窗口不完整，未补成交")
                    if symbol.startswith("^"):
                        warnings.append("指数时段以 XNYS 日历为参考，未验证指数专属日历")
                    closed_sessions = self._exchange().sessions_in_range(
                        pd.Timestamp(now.astimezone(ET).date() - timedelta(days=14)),
                        pd.Timestamp(now.astimezone(ET).date()))
                    closed_sessions = [label for label in closed_sessions
                                       if self._exchange().session_close(label).to_pydatetime() <= now]
                    old_source = False
                    if closed_sessions:
                        latest_day = date.fromisoformat(bars[-1]["date"])
                        latest_covered = self._period_close(latest_day, interval) if interval not in MINUTES else self._day_bounds(latest_day)[1]
                        old_source = latest_covered < self._exchange().session_close(closed_sessions[-1]).to_pydatetime()
                    if old_source:
                        warnings.append("来源最新数据落后于最近已结束交易日")
                    metadata = {"source": self.source, "feed": self.feed,
                                "as_of": bars[-1]["timestamp"],
                                "as_of_kind": "bar_start" if interval in MINUTES else "trading_date",
                                "received_at": now.isoformat(), "cache": "fresh",
                                "quality": "stale" if old_source else "partial" if incomplete or missing or dropped or gaps else "complete",
                                "delay_seconds": None, "delay_label": "来源未承诺实时延迟",
                                "coverage": {"available_count": len(bars), "returned_count": len(bars),
                                             "dropped_count": dropped, "duplicate_count": duplicates,
                                             "expected_regular_count": expected_regular,
                                             "available_regular_count": available_regular,
                                             "completion_basis": "calendar_interval_elapsed",
                                             "calendar_reference": "XNYS", "calendar_verified": not symbol.startswith("^")},
                                "warning": "；".join(warnings) or None}
                    try:
                        self._write_cache(path, bars, metadata)
                    except OSError:
                        metadata["warning"] = "；".join(filter(None, [metadata["warning"], "缓存写入失败，当前为新获取数据"]))
                except Exception:
                    if cached:
                        bars, metadata = cached
                        metadata = deepcopy(metadata)
                        cache_state = "stale"
                        metadata["quality"] = "stale"
                        metadata["warning"] = "上游当前不可用；返回已缓存行情，未伪装为实时数据。"
                    else:
                        raise MarketDataError("market_data_unavailable", "行情来源暂不可用或没有覆盖此标的。", 503) from None
            metadata = deepcopy(metadata)
            metadata["cache"] = cache_state
            metadata["coverage"]["returned_count"] = min(len(bars), limit)
            return {"symbol": symbol, "interval": interval, "period": period, "session": session,
                    "adjustment": adjustment, "adjustment_label": ADJUSTMENTS[adjustment],
                    "bars": deepcopy(bars[-limit:]), "meta": metadata}

    def get_quotes(self, symbols: list[str]) -> dict:
        symbols = list(dict.fromkeys(_symbol(symbol) for symbol in symbols))
        if not 1 <= len(symbols) <= 50:
            raise MarketDataError("invalid_symbol_count", "一次查询须为 1 至 50 个证券", 422)

        def one(symbol):
            try:
                result = self.get_bars(symbol, period="1mo")
                usable = [bar for bar in result["bars"] if bar["close"] is not None]
                latest = usable[-1] if usable else None
                current_available = latest is not None and latest is result["bars"][-1]
                previous = result["bars"][-2]["close"] if current_available and len(result["bars"]) > 1 else None
                price = latest["close"] if latest else None
                change = price - previous if price is not None and previous is not None else None
                quote_meta = deepcopy(result["meta"])
                if latest and not current_available:
                    quote_meta["as_of"] = latest["timestamp"]
                    quote_meta["warning"] = "最新柱缺少收盘字段，仅显示最近有效柱，未计算当日涨跌。"
                return {"symbol": symbol, "price": price, "previous_close": previous, "change": change,
                        "change_percent": change / previous * 100 if change is not None and previous else None,
                        "volume": latest["volume"] if latest else None, "price_kind": "bar_close",
                        "timestamp": latest["timestamp"] if latest else None, "meta": quote_meta}
            except MarketDataError:
                return {"symbol": symbol, "price": None, "previous_close": None, "change": None,
                        "change_percent": None, "volume": None, "price_kind": "bar_close", "timestamp": None,
                        "meta": {"source": self.source, "feed": self.feed, "as_of": None,
                                 "as_of_kind": "trading_date", "received_at": self._now().isoformat(),
                                 "cache": "fresh", "quality": "unavailable", "delay_seconds": None,
                                 "delay_label": "来源未承诺实时延迟", "coverage": {},
                                 "warning": "来源未返回可用行情；未使用模拟或零值替代。"}}

        with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
            return {"quotes": list(pool.map(one, symbols))}

    def get_calendar(self, start: str, end: str) -> dict:
        try:
            begin, finish = date.fromisoformat(start), date.fromisoformat(end)
            if begin > finish or (finish - begin).days > 366:
                raise ValueError
            sessions = self._exchange().sessions_in_range(pd.Timestamp(begin), pd.Timestamp(finish))
        except (ValueError, TypeError):
            raise MarketDataError("invalid_calendar_window", "日历窗口须为有效日期且不超过 366 天", 422) from None
        rows = []
        for label in sessions:
            opening = self._exchange().session_open(label).to_pydatetime()
            closing = self._exchange().session_close(label).to_pydatetime()
            rows.append({"date": label.date().isoformat(), "open": opening.isoformat(),
                         "close": closing.isoformat(), "is_early_close": closing.astimezone(ET).time() < time(16)})
        now = self._now()
        bounds = self._day_bounds(now.astimezone(ET).date())
        state = "closed"
        if bounds:
            opening, closing = bounds
            local = now.astimezone(ET)
            if opening <= now < closing:
                state = "regular"
            elif time(4) <= local.time() and now < opening:
                state = "pre"
            elif now >= closing and local.time() < time(20):
                state = "post"
        return {"calendar": "XNYS", "timezone": "America/New_York", "sessions": rows,
                "current_state": state, "received_at": now.isoformat(), "source": "exchange-calendars"}

    def get_indicators(self, symbol: str, *, interval="1d", period="3mo", session="regular",
                       adjustment="provider_native", sma_period=20, ema_period=20, rsi_period=14,
                       macd_fast=12, macd_slow=26, macd_signal=9, boll_period=20,
                       boll_deviation=2, atr_period=14) -> dict:
        periods = {"sma_period": sma_period, "ema_period": ema_period, "rsi_period": rsi_period,
                   "macd_fast": macd_fast, "macd_slow": macd_slow, "macd_signal": macd_signal,
                   "boll_period": boll_period, "atr_period": atr_period}
        if (any(isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 200 for value in periods.values())
                or macd_fast >= macd_slow or not math.isfinite(boll_deviation) or not 0.1 <= boll_deviation <= 5):
            raise MarketDataError("invalid_indicator_parameters", "指标参数无效，MACD 快线须短于慢线", 422)
        data = self.get_bars(symbol, interval=interval, period=period, session=session, adjustment=adjustment)
        frame = pd.DataFrame(data["bars"])
        close, high, low = (pd.to_numeric(frame[key], errors="coerce") for key in ("close", "high", "low"))
        average = close.rolling(boll_period, min_periods=boll_period).mean()
        deviation = close.rolling(boll_period, min_periods=boll_period).std(ddof=0)
        delta = close.diff()
        gain = pd.Series(wilder_smoothing(delta.clip(lower=0), rsi_period), index=close.index)
        loss = pd.Series(wilder_smoothing(-delta.clip(upper=0), rsi_period), index=close.index)
        rsi = 100 - 100 / (1 + gain / loss)
        rsi = rsi.mask((gain == 0) & (loss == 0), 50).where(delta.notna())
        macd = close.ewm(span=macd_fast, adjust=False, min_periods=macd_fast).mean() - close.ewm(
            span=macd_slow, adjust=False, min_periods=macd_slow).mean()
        signal = macd.ewm(span=macd_signal, adjust=False, min_periods=macd_signal).mean()
        previous = close.shift(1)
        ranges = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1)
        true_range = ranges.max(axis=1, skipna=False)
        series = {"sma": close.rolling(sma_period, min_periods=sma_period).mean(),
                  "ema": close.ewm(span=ema_period, adjust=False, min_periods=ema_period).mean(),
                  "rsi": rsi, "macd": macd, "macd_signal": signal, "macd_histogram": macd - signal,
                  "boll_upper": average + boll_deviation * deviation, "boll_middle": average,
                  "boll_lower": average - boll_deviation * deviation,
                  "atr": pd.Series(wilder_smoothing(true_range, atr_period), index=close.index)}
        points = []
        for position, bar in enumerate(data["bars"]):
            point = {"timestamp": bar["timestamp"], "date": bar["date"]}
            point.update({name: _number(values.iloc[position]) if close.notna().iloc[position] else None
                          for name, values in series.items()})
            points.append(point)
        return {"symbol": data["symbol"], "interval": interval,
                "parameters": {**periods, "boll_deviation": boll_deviation},
                "method": {"version": "numeric-v2", "rsi": WILDER_METHOD, "atr": WILDER_METHOD,
                           "boll_std_ddof": 0, "warmup": "不足预热窗口或原始价格缺失返回 null",
                           "wilder_seed": "连续N个有效变化/TR的算术均值；缺失值后重新预热；首柱无前收，不生成TR",
                           "tracking_comparison": "与跟踪表共用Wilder种子算法；图表使用返回范围，跟踪表固定65个完整日。需统一来源、复权、会话、截止日、周期和种子窗口后再比较。",
                           "research_comparison": "研究工具 stockstats 的具体默认参数可能不同，比较前须统一口径。"},
                "points": points, "meta": data["meta"]}
