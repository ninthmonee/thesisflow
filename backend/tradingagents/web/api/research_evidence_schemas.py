"""Public, scoped research evidence and independent data qualification."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import ContractModel


class ResearchEvidenceIssue(ContractModel):
    code: str
    message: str


class ResearchQualitySummary(ContractModel):
    status: Literal["usable", "degraded", "insufficient", "unverified"]
    rule_version: Literal["research-quality-v1", "local-agent-evidence-v1"] = "research-quality-v1"
    checked_at: datetime | None = None
    evidence_count: int = Field(ge=0)
    validated_price: bool
    eligible_for_rating: bool
    eligible_for_levels: bool
    eligible_for_sizing: bool
    required_categories: list[str]
    available_categories: list[str]
    missing_categories: list[str]
    issues: list[ResearchEvidenceIssue]
    scope_notice: str = (
        "资格仅说明工具证据的可用性与字段检查，不证明模型论据正确、未来收益或个人适当性；"
        "原始模型报告保留，未通过资格的评级与价格/仓位不作为有效结论发布。"
    )


class ResearchEvidenceItem(ContractModel):
    id: str
    snapshot_id: str | None
    attempt_id: str | None
    method: str
    category: str
    provider: str
    status: Literal["validated", "available", "unknown", "unavailable", "error"]
    request: dict[str, str | int | float | bool | None]
    requested_as_of: str | None
    data_as_of: str | None
    published_at: datetime | None
    received_at: datetime
    response_hash: str | None
    stored_bytes: int = Field(ge=0)
    truncated: bool
    body: str | None = None
    issues: list[ResearchEvidenceIssue] = Field(default_factory=list)
    capture_version: Literal["research-evidence-v1"] = "research-evidence-v1"


class ResearchEvidencePage(ContractModel):
    run_id: str
    items: list[ResearchEvidenceItem]
    next_cursor: str | None = None
    capture_started: bool
    quality: ResearchQualitySummary
    scope_notice: str = (
        "仅列本次研究实际经过捕获链路的工具数据；正文为有界白名单摘录，未知结构不保存原文。"
        "仅覆盖已适配工具和已接入的社交预取；未知结构或其它私有工具保留未知，"
        "白名单摘录不代表全部原始输入已归档或可公平复跑。"
    )
