"""A view requires the human's thesis and invalidation, not an automatic trade."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .base import ContractModel

Stance = Literal["watch", "positive", "neutral", "negative"]
ViewpointStatus = Literal["active", "invalidated", "archived"]


class ViewpointWrite(ContractModel):
    stance: Stance = "watch"
    thesis: str = Field(min_length=1, max_length=3000)
    invalidation: str = Field(min_length=1, max_length=2000)
    review_on: str | None = Field(default=None, min_length=10, max_length=10)
    review_notes: str = Field(default="", max_length=3000)

    @field_validator("thesis", "invalidation")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("观点及失效条件不能为空")
        return value.strip()

    @field_validator("review_on")
    @classmethod
    def valid_review_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("复核日期无效")
        return value


class ViewpointCreate(ViewpointWrite):
    run_id: UUID
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("须由用户明确确认观点")
        return value


class ViewpointUpdate(ViewpointWrite):
    revision: int = Field(ge=1, strict=True)
    status: ViewpointStatus = "active"
    mark_reviewed: bool = False


class ViewpointView(ViewpointWrite):
    id: str
    run_id: str
    ticker: str
    status: ViewpointStatus
    revision: int
    review_due: bool
    confirmed_at: datetime
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    source_quality: str
    scope_notice: str = "用户手动确认的研究观点与复核记录；不代表已持仓、自动交易、后台定时监控或收益验证。"


class ViewpointList(ContractModel):
    items: list[ViewpointView]


class ResearchComparison(ContractModel):
    run_id: str
    other_run_id: str
    ticker: str
    request_changes: list[str]
    model_changed: bool
    same_captured_evidence: bool | None
    changed_sections: list[str]
    section_diffs: dict[str, str]
    scope_notice: str = "只对比保存的请求、模型快照、已捕获证据哈希和报告文本；数据不同时不能归因为模型差异，文本变化不是策略绩效。"
