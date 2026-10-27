"""Transparent, data-only technical observations for privately followed securities."""

from datetime import datetime
from typing import Literal

from pydantic import Field, FiniteFloat

from .schemas import ContractModel


class TrackingError(Exception):
    def __init__(self, code, message, status_code=400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class TrackingRuleDefinition(ContractModel):
    id: str
    label: str
    description: str


class TrackingRules(ContractModel):
    version: Literal["tracking-v1"] = "tracking-v1"
    configurable: Literal[False] = False
    required_sessions: Literal[65] = 65
    volatility_thresholds: list[float] = Field(default_factory=lambda: [1, 2, 3, 5])
    formulas: list[TrackingRuleDefinition]
    references: list[str]
    scope_notice: str = (
        "固定版本机械技术观察，未验证预测胜率；不是估值、投资建议、信用评级或交易指令。"
    )


class TrackingCoverage(ContractModel):
    required_sessions: int = 65
    available_sessions: int
    missing_sessions: int
    invalid_price_sessions: int
    duplicate_sessions: int
    excluded_unclosed: int
    window_start: str | None
    window_end: str | None
    expected_latest_session: str | None


class TrackingMeta(ContractModel):
    source: str | None
    feed: str | None
    as_of: str | None
    previous_as_of: str | None
    received_at: datetime
    source_received_at: datetime | None
    source_quality: str | None
    source_warning: str | None
    quality: Literal["complete", "partial", "stale", "unavailable"]
    cache: Literal["fresh", "hit", "stale"] = "fresh"
    coverage: TrackingCoverage
    warnings: list[str] = Field(default_factory=list)


class WatchlistTrackingItem(ContractModel):
    instrument_id: str
    symbol: str
    name: str
    asset_type: str
    currency: str
    price_kind: Literal["completed_daily_close"] = "completed_daily_close"
    price: FiniteFloat | None
    previous_close: FiniteFloat | None
    change: FiniteFloat | None
    change_percent: FiniteFloat | None
    ma20: FiniteFloat | None
    ma50: FiniteFloat | None
    atr14: FiniteFloat | None
    atr_percent: FiniteFloat | None
    trend: Literal["uptrend", "downtrend", "mixed", "unknown"]
    observation: Literal[
        "above_range", "below_range", "near_upper", "near_lower", "inside_range", "unavailable"
    ]
    range_low: FiniteFloat | None
    range_high: FiniteFloat | None
    reference_target: FiniteFloat | None
    protection_level: FiniteFloat | None
    technical_volatility_level: int | None = Field(ge=1, le=5)
    eligible_for_levels: bool
    rules_version: Literal["tracking-v1"] = "tracking-v1"
    rule_reasons: list[str]
    meta: TrackingMeta


class WatchlistTrackingResponse(ContractModel):
    items: list[WatchlistTrackingItem]
    rules: TrackingRules
    received_at: datetime
    total_count: int
    requested_count: int
    returned_count: int
    truncated: bool
    refresh_throttled: bool = False
    watchlist_id: str | None
    scope_notice: str = "仅当前用户已关注的美国上市股票/ETF观察；跨组按instrument_id去重，价格是最近完整日收盘，不是盘中实时报价。"
