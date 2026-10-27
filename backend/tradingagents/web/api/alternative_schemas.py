"""Explicitly scoped ETF flow, option and publisher-sample sentiment contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, FiniteFloat, SecretStr, field_validator

from .schemas import ContractModel

AlternativeProvider = Literal["massive", "intrinio", "alpaca"]
AlternativeCapability = Literal["news", "options", "etf_flows"]
AlternativeQuality = Literal[
    "complete", "partial", "unavailable", "stale", "configuration_required", "action_required"
]


class AlternativeDataError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class DataConnectionCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    provider: AlternativeProvider
    api_key: SecretStr
    api_secret: SecretStr | None = None
    feed: Literal["indicative", "opra"] = "indicative"
    enabled: bool = True


class DataConnectionUpdate(ContractModel):
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    feed: Literal["indicative", "opra"] | None = None
    enabled: bool | None = None


class DataConnectionView(ContractModel):
    id: str
    name: str
    provider: AlternativeProvider
    feed: str
    enabled: bool
    revision: int
    credential_configured: bool
    secondary_credential_configured: bool
    test_results: dict[str, str]
    tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DataConnectionList(ContractModel):
    connections: list[DataConnectionView]


class DataConnectionTestRequest(ContractModel):
    revision: int = Field(ge=1, strict=True)
    capability: AlternativeCapability
    symbol: str = Field(default="NVDA", min_length=1, max_length=32)


class DataConnectionTestResponse(ContractModel):
    connection: DataConnectionView
    capability: AlternativeCapability
    success: bool
    message: str


class AlternativeMeta(ContractModel):
    source: str
    feed: str
    as_of: str | None
    received_at: datetime
    quality: AlternativeQuality
    cache: Literal["fresh", "hit", "stale"] = "fresh"
    delay_label: str
    scope: str
    is_estimated: bool = False
    coverage: dict[str, int | str | bool | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AlternativeSourceCapability(ContractModel):
    id: str
    name: str
    capabilities: list[AlternativeCapability]
    authentication: str
    configured: bool
    free_or_existing_account: bool
    scope_notice: str
    documentation_url: str


class AlternativeCapabilities(ContractModel):
    sources: list[AlternativeSourceCapability]
    issuer_etfs: list[str]
    trading_enabled: Literal[False] = False


class ETFFlowPoint(ContractModel):
    date: str
    as_of_date: str | None
    nav: FiniteFloat | None
    shares_outstanding: FiniteFloat | None
    net_flow_usd: FiniteFloat | None
    flow_kind: Literal["estimated", "vendor_reported"]
    quality: AlternativeQuality
    method: str
    reason: str | None = None


class ETFFlowSeries(ContractModel):
    symbol: str
    points: list[ETFFlowPoint]
    meta: AlternativeMeta


class ETFFlowsResponse(ContractModel):
    funds: list[ETFFlowSeries]
    scope_notice: str = "ETF份额创设/赎回口径；估算值不是发行商已报告净申赎，不能代表机构净买入。"


class OptionContract(ContractModel):
    symbol: str
    expiration: str
    contract_type: Literal["call", "put"]
    strike: FiniteFloat
    multiplier: FiniteFloat | None = None
    bid: FiniteFloat | None = None
    ask: FiniteFloat | None = None
    last_price: FiniteFloat | None = None
    volume: FiniteFloat | None = None
    open_interest: FiniteFloat | None = None
    implied_volatility: FiniteFloat | None = None
    delta: FiniteFloat | None = None
    gamma: FiniteFloat | None = None
    theta: FiniteFloat | None = None
    vega: FiniteFloat | None = None
    last_trade_at: datetime | None = None
    quote_as_of: datetime | None = None
    open_interest_as_of: str | None = None

    @field_validator(
        "strike", "bid", "ask", "last_price", "volume", "open_interest", "implied_volatility"
    )
    @classmethod
    def nonnegative(cls, value):
        if value is not None and value < 0:
            raise ValueError("期权价格和数量不能为负")
        return value


class OptionsResponse(ContractModel):
    underlying: str
    available_expirations: list[str]
    selected_expirations: list[str]
    contracts: list[OptionContract]
    put_call_volume_ratio: FiniteFloat | None
    put_call_oi_ratio: FiniteFloat | None
    ratio_scope: str = "仅本次返回合约的聚合；不是全市场put/call，不以缺失值补零。"
    meta: AlternativeMeta


class NewsSentimentArticle(ContractModel):
    id: str
    title: str
    url: str | None
    publisher: str
    published_at: datetime
    tickers: list[str]
    positive_observations: int = 0
    negative_observations: int = 0
    neutral_observations: int = 0
    missing_sentiment: bool


class NewsSentimentResponse(ContractModel):
    articles: list[NewsSentimentArticle]
    article_count: int
    publisher_count: int
    ticker_count: int
    positive: int
    negative: int
    neutral: int
    unclassified_articles: int
    sentiment_balance: FiniteFloat | None
    method: str = (
        "(正面ticker情绪观察数−负面观察数)/已分类观察数；同一篇新闻可含多个ticker，去重文章后计数。"
    )
    meta: AlternativeMeta
