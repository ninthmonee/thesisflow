"""Numeric market-data contracts, independent from providers and cache runtimes."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .schemas import ContractModel

MarketInterval = Literal["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"]
MarketPeriod = Literal["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]
MarketSession = Literal["regular", "extended"]
MarketAdjustment = Literal["provider_native", "dividend_adjusted"]


class MarketDataError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class MarketMeta(ContractModel):
    source: str
    feed: str
    as_of: datetime | None
    as_of_kind: str = "bar_start"
    received_at: datetime
    cache: Literal["fresh", "hit", "stale"]
    quality: Literal["complete", "partial", "stale", "unavailable"]
    delay_seconds: float | None = None
    delay_label: str = "来源未承诺实时延迟"
    coverage: dict[str, Any] = Field(default_factory=dict)
    warning: str | None = None


class MarketBar(ContractModel):
    timestamp: datetime
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    session: Literal["regular", "pre", "post"]
    is_complete: bool


class BarsResponse(ContractModel):
    symbol: str
    interval: MarketInterval
    period: MarketPeriod
    session: MarketSession
    adjustment: MarketAdjustment
    adjustment_label: str
    bars: list[MarketBar]
    meta: MarketMeta


class Quote(ContractModel):
    symbol: str
    price: float | None
    previous_close: float | None
    change: float | None
    change_percent: float | None
    volume: float | None
    price_kind: Literal["bar_close"] = "bar_close"
    timestamp: datetime | None
    meta: MarketMeta


class QuotesResponse(ContractModel):
    quotes: list[Quote]


class IndicatorPoint(ContractModel):
    timestamp: datetime
    date: str
    sma: float | None
    ema: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    boll_upper: float | None
    boll_middle: float | None
    boll_lower: float | None
    atr: float | None


class IndicatorsResponse(ContractModel):
    symbol: str
    interval: MarketInterval
    parameters: dict[str, Any]
    method: dict[str, Any]
    points: list[IndicatorPoint]
    meta: MarketMeta


class CalendarSession(ContractModel):
    date: str
    open: datetime
    close: datetime
    is_early_close: bool


class CalendarResponse(ContractModel):
    calendar: Literal["XNYS"] = "XNYS"
    timezone: Literal["America/New_York"] = "America/New_York"
    sessions: list[CalendarSession]
    current_state: Literal["pre", "regular", "post", "closed"]
    received_at: datetime
    source: str = "exchange-calendars"


class MarketCapabilities(ContractModel):
    source: str
    feed: str
    intervals: list[str]
    periods: list[str]
    interval_periods: dict[str, list[str]]
    sessions: list[str]
    adjustments: dict[str, str]
    quote_kind: Literal["bar_close"] = "bar_close"
    realtime_guaranteed: Literal[False] = False
    nbbo: Literal[False] = False
    warning: str
