"""Market-intelligence contracts; importing them never loads a data provider."""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field, FiniteFloat

from .schemas import ContractModel, Language

MarketWindow = Literal["1D", "5D", "1M"]
DataQuality = Literal["complete", "partial", "stale", "unavailable"]
PulseAvailability = Literal[
    "disabled", "available", "configuration_required", "unavailable", "error"
]


class IntelligenceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class ProfessionalPulseBaseline(ContractModel):
    status: Literal["available", "unknown"] = "unknown"
    as_of: str | None = None
    observed_at: datetime | None = None
    deltas: dict[str, FiniteFloat | None] = Field(default_factory=dict)
    reason: str


class ProfessionalPulseMeta(ContractModel):
    availability: PulseAvailability
    source: str
    feed: str | None = None
    as_of: str | None = None
    received_at: datetime
    quality: Literal["complete", "partial", "stale", "unavailable", "action_required"]
    coverage: dict[str, int | str | bool | None] = Field(default_factory=dict)
    scope: str
    method: str
    warnings: list[str] = Field(default_factory=list)
    baseline: ProfessionalPulseBaseline


class ProfessionalOptionsPulse(ContractModel):
    symbol: str
    contract_count: int | None = Field(default=None, ge=0)
    expiration_count: int | None = Field(default=None, ge=0)
    put_call_volume_ratio: FiniteFloat | None = None
    put_call_oi_ratio: FiniteFloat | None = None
    meta: ProfessionalPulseMeta


class ProfessionalETFFlowPulse(ContractModel):
    symbol: str
    flow_kind: Literal["estimated", "vendor_reported", "mixed", "unknown"]
    point_count: int | None = Field(default=None, ge=0)
    valid_point_count: int | None = Field(default=None, ge=0)
    window_start: str | None = None
    window_end: str | None = None
    latest_flow_usd: FiniteFloat | None = None
    available_points_sum_usd: FiniteFloat | None = None
    meta: ProfessionalPulseMeta


class ProfessionalNewsPulse(ContractModel):
    symbol: str | None = None
    article_count: int | None = Field(default=None, ge=0)
    publisher_count: int | None = Field(default=None, ge=0)
    ticker_count: int | None = Field(default=None, ge=0)
    positive: int | None = Field(default=None, ge=0)
    negative: int | None = Field(default=None, ge=0)
    neutral: int | None = Field(default=None, ge=0)
    unclassified_articles: int | None = Field(default=None, ge=0)
    sentiment_balance: FiniteFloat | None = None
    meta: ProfessionalPulseMeta


class ProfessionalMarketPulse(ContractModel):
    id: str
    received_at: datetime
    sample_signature: str
    options_availability: PulseAvailability
    flows_availability: PulseAvailability
    news_availability: PulseAvailability
    options: list[ProfessionalOptionsPulse]
    etf_flows: list[ProfessionalETFFlowPulse]
    news: ProfessionalNewsPulse | None = None
    warnings: list[str]
    scope_notice: str = (
        "只汇总用户主动选择的期权标的、ETF和供应商新闻样本；三类证据独立，"
        "不合成全市场情绪、不推断机构净买入。基线只是同口径两次本机观察差值，"
        "滚动窗口可重叠，不是交易信号或经验证的历史回测。"
    )


class MetricEvidence(ContractModel):
    id: str
    label: str
    value: FiniteFloat | None = None
    unit: str
    as_of: str | None
    source: str | None
    feed: str | None
    quality: DataQuality
    method: str


class BenchmarkMetric(ContractModel):
    symbol: str
    name: str
    kind: Literal["etf_proxy", "volatility_index"]
    price: FiniteFloat | None
    return_percent: FiniteFloat | None
    as_of: str | None
    quality: DataQuality
    reason: str | None = None
    evidence_ids: list[str]


class SectorMetric(ContractModel):
    symbol: str
    name: str
    return_percent: FiniteFloat | None
    relative_return_pp: FiniteFloat | None
    volume: FiniteFloat | None
    turnover_estimate_usd: FiniteFloat | None
    turnover_method: str = "最近完整交易日收盘价×当日成交量，仅为单日成交额估算，不代表净流入"
    daily_rvol: FiniteFloat | None
    rvol_sample_count: int = Field(ge=0)
    rvol_method: str = "最近完整交易日成交量/此前20个交易日完整日量平均值，同一feed；缺失日量不计算"
    as_of: str | None
    quality: DataQuality
    reason: str | None = None
    evidence_ids: list[str]


class BreadthMetric(ContractModel):
    universe_name: str
    universe_version: str
    universe_symbols: list[str]
    scope_notice: str = "固定观察样本，非全市场；等权计数，不代表标普500成分股"
    requested_count: int = Field(ge=0)
    available_count: int = Field(ge=0)
    advancers: int | None = Field(ge=0)
    decliners: int | None = Field(ge=0)
    unchanged: int | None = Field(ge=0)
    above_sma20_percent: FiniteFloat | None
    sma20_sample_count: int = Field(ge=0)
    quality: DataQuality
    evidence_ids: list[str]


class OptionalMarketData(ContractModel):
    id: str
    name: str
    state: Literal["unavailable"] = "unavailable"
    reason: str


class MarketObservation(ContractModel):
    text: str
    evidence_ids: list[str] = Field(min_length=1)


class MarketSnapshot(ContractModel):
    id: str = Field(pattern=r"^mkt-[0-9a-f]{32}$")
    schema_version: Literal["1"] = "1"
    window: MarketWindow
    window_start: str | None
    window_end: str | None
    as_of: str | None
    as_of_precision: Literal["completed_trading_day"] = "completed_trading_day"
    received_at: datetime
    source: str | None
    feed: str | None
    adjustment: str = "provider_native"
    basis: str = (
        "日级已完成交易日；价格收益，不承诺总回报；所有相对比较使用相同来源/feed和共同截止日"
    )
    quality: DataQuality
    benchmarks: list[BenchmarkMetric]
    sectors: list[SectorMetric]
    breadth: BreadthMetric
    evidence: list[MetricEvidence]
    observed_divergences: list[MarketObservation]
    optional_data: list[OptionalMarketData]
    warnings: list[str]


class BriefClaim(ContractModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class BriefContent(ContractModel):
    state: BriefClaim
    changes: list[BriefClaim] = Field(default_factory=list, max_length=10)
    attention: list[BriefClaim] = Field(default_factory=list, max_length=10)
    divergences: list[BriefClaim] = Field(default_factory=list, max_length=10)
    risks: list[BriefClaim] = Field(default_factory=list, max_length=10)


class BriefGeneration(ContractModel):
    content: BriefContent
    model: str | None = None
    provider: str | None = None


class MarketBriefRequest(ContractModel):
    snapshot_id: str = Field(pattern=r"^mkt-[0-9a-f]{32}$")
    language: Language = "Chinese"


class MarketBrief(ContractModel):
    id: str
    snapshot_id: str
    as_of: str
    generated_at: datetime
    model: str | None
    provider: str | None
    content: BriefContent
    observed_divergences: list[MarketObservation]
    data_gaps: list[str]
    scope_notice: str


class IntelligenceServiceProtocol(Protocol):
    def get_snapshot(self, window: MarketWindow = "1D", *, refresh: bool = False) -> Any: ...
    def generate_brief(
        self, snapshot_id: str, language: Language = "Chinese", *, generator=None
    ) -> Any: ...
