"""Scoped, versioned thematic observation baskets and their honest analytics."""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, FiniteFloat, field_validator

from .base import ContractModel

ThemeCategory = Literal["technology", "materials", "energy", "healthcare", "custom"]
ThemeExposure = Literal[
    "direct_product", "diversified_business", "manufacturing_service", "supplier", "user_unverified"
]
ThemeEvidenceQuality = Literal["documented", "incomplete", "unverified"]


class ThemeTaxonomyTag(ContractModel):
    kind: Literal["industry", "theme", "material", "value_chain"]
    label: str = Field(min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        if not value.strip():
            raise ValueError("分类标签不能为空")
        return value.strip()


class ThemeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class ThemeMemberInput(ContractModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9^][A-Za-z0-9.^=_\-]*$")
    name: str = Field(default="", max_length=240)
    role: str = Field(default="", max_length=240)
    exposure: ThemeExposure = "user_unverified"
    evidence_urls: list[str] = Field(default_factory=list, max_length=8)
    verified_at: str | None = Field(default=None, max_length=10)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value):
        symbol = value.strip().upper()
        # Dot-delimited foreign listings have local currencies and calendars.
        # A/B share classes are US ticker notation, not exchange suffixes.
        if (
            ("." in symbol and not re.fullmatch(r"[A-Z0-9]+\.[AB]", symbol))
            or "=" in symbol
            or symbol.startswith("^")
            or symbol.endswith(("-USD", "-USDT", "-USDC"))
        ):
            raise ValueError(
                "主题成员当前仅支持美股、美国上市ADR和ETF；不支持海外市场后缀或币种转换"
            )
        return symbol

    @field_validator("evidence_urls")
    @classmethod
    def public_urls(cls, values):
        for value in values:
            parsed = urlsplit(value)
            if (
                len(value) > 1500
                or parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("证据链接须为不含账号密码的HTTP(S)地址")
        return list(dict.fromkeys(values))

    @field_validator("verified_at")
    @classmethod
    def checked_date(cls, value):
        if value is not None:
            from datetime import date

            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("核实日期无效")
        return value


def member_evidence_quality(member: ThemeMemberInput) -> ThemeEvidenceQuality:
    """Assess completeness of maintainer-supplied records, never verify a relationship."""
    if member.exposure == "user_unverified":
        return "unverified"
    return "documented" if member.evidence_urls and member.verified_at else "incomplete"


class ThemeMemberView(ThemeMemberInput):
    evidence_quality: ThemeEvidenceQuality


class ThemeWrite(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: ThemeCategory = "custom"
    aliases: list[str] = Field(default_factory=list, max_length=20)
    taxonomy_tags: list[ThemeTaxonomyTag] = Field(default_factory=list, max_length=24)
    members: list[ThemeMemberInput] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("主题名称不能为空")
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values):
        if any(not value.strip() or len(value) > 120 for value in values):
            raise ValueError("主题别名无效")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("members")
    @classmethod
    def unique_members(cls, values):
        if len({value.symbol for value in values}) != len(values):
            raise ValueError("同一主题内的证券不能重复")
        return values

    @field_validator("taxonomy_tags")
    @classmethod
    def unique_taxonomy_tags(cls, values):
        keys = [(value.kind, value.label.casefold()) for value in values]
        if len(set(keys)) != len(keys):
            raise ValueError("同一维度的分类标签不能重复")
        return values


class ThemeUpdate(ThemeWrite):
    revision: int = Field(ge=1, strict=True)


class ThemeRevision(ContractModel):
    revision: int = Field(ge=1, strict=True)


class ThemePin(ThemeRevision):
    pinned: bool = Field(strict=True)


class ThemeClone(ThemeRevision):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("主题名称不能为空")
        return value.strip()


class ThemeMemberAdd(ThemeRevision):
    member: ThemeMemberInput


class ThemeView(ThemeWrite):
    members: list[ThemeMemberView] = Field(default_factory=list, max_length=100)
    id: str
    revision: int
    version: int
    is_builtin: bool
    archived: bool
    pinned: bool = False
    effective_from: datetime
    created_at: datetime
    updated_at: datetime
    scope_notice: str = "自定义观察样本，非完整行业成分；主题可重叠，不构成投资推荐。"
    evidence_notice: str = (
        "关系证据仅表示维护者提供了类型、核查日期及公开链接，不表示平台验证或持续有效。"
    )


class ThemeList(ContractModel):
    items: list[ThemeView]


class ThemeVersionList(ContractModel):
    items: list[ThemeView]


class ThemeMemberMetric(ContractModel):
    symbol: str
    name: str
    role: str
    exposure: ThemeExposure
    evidence_urls: list[str]
    verified_at: str | None
    evidence_quality: ThemeEvidenceQuality
    return_percent: FiniteFloat | None
    relative_return_pp: FiniteFloat | None
    daily_rvol: FiniteFloat | None
    rvol_sample_count: int = Field(ge=0)
    turnover_estimate_usd: FiniteFloat | None
    as_of: str | None
    source: str | None
    feed: str | None
    quality: Literal["complete", "partial", "stale", "unavailable"]
    reason: str | None


class ThemeAnalytics(ContractModel):
    theme_id: str
    theme_name: str
    version: int
    window: Literal["1D", "5D", "1M"]
    window_start: str | None
    window_end: str | None
    received_at: datetime
    source: str | None
    feed: str | None
    adjustment: str = "provider_native"
    membership_basis: str = (
        "按所选主题版本的固定成分计算最近完整交易日窗口，不代表当时已知成分或历史回测。"
    )
    membership_effective_from: datetime
    window_basis: Literal["latest_completed_sessions"] = "latest_completed_sessions"
    documented_only: bool = False
    original_member_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    method: str = "有效样本等权价格收益；同来源/feed/调整口径及SPY共同交易日起止，不承诺总回报。"
    attention_method: str = (
        "日RVOL为完整日成交量/此前20个完整交易日均量；不把不同股票成交股数相加当关注度。"
    )
    turnover_method: str = "完整日收盘价×成交量的估算，仅表示成交额，不代表净流入。"
    requested_count: int
    available_count: int
    return_percent: FiniteFloat | None
    relative_return_pp: FiniteFloat | None
    spy_return_percent: FiniteFloat | None
    advancers: int | None
    decliners: int | None
    unchanged: int | None
    median_daily_rvol: FiniteFloat | None
    quality: Literal["complete", "partial", "stale", "unavailable"]
    members: list[ThemeMemberMetric]
    warnings: list[str]
