"""Presentation-safe API contracts shared by runtime routes and offline codegen."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, RootModel, field_validator

from .base import ContractModel
from .research_evidence_schemas import ResearchQualitySummary
from .research_runtime_schemas import RunAttemptStats, UsageQuality

Analyst = Literal["market", "social", "news", "fundamentals"]
Language = Literal["Chinese", "English"]
CapabilityState = Literal["available", "configuration_required", "planned"]
RunStatus = Literal[
    "pending", "queued", "running", "cancelling", "cancelled", "completed", "failed", "interrupted"
]


class ApplicationInfo(ContractModel):
    name: Literal["TradingAgents"] = "TradingAgents"
    version: str
    mode: Literal["local"] = "local"


class WorkspaceInfo(ContractModel):
    id: str
    name: str
    owner_id: str


class ResearchDefaults(ContractModel):
    analysts: list[Analyst] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"]
    )
    depth: Literal[1, 2, 3] = 1
    language: Language = "Chinese"


class ModelStatus(ContractModel):
    configured: bool
    provider: str
    quick_provider: str
    deep_provider: str
    quick_model: str | None = None
    deep_model: str | None = None
    status: Literal["configured", "missing", "unknown"]
    verification: Literal["not_checked"] = "not_checked"


class ServiceStatus(ContractModel):
    id: str
    name: str
    configured: bool
    status: str


class Capability(ContractModel):
    id: str
    label: str
    state: CapabilityState
    reason: str | None = None


class BootstrapResponse(ContractModel):
    api_version: Literal["1"] = "1"
    application: ApplicationInfo
    workspace: WorkspaceInfo
    today: str | None = None
    defaults: ResearchDefaults
    model: ModelStatus
    services: list[ServiceStatus]
    capabilities: list[Capability]
    active_run_id: str | None = None


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["1"] = "1"
    mode: Literal["local"] = "local"


class ApiError(ContractModel):
    error_code: str
    message: str
    retryable: bool = False
    trace_id: str | None = None


class CreateResearchRequest(ContractModel):
    """Legacy-compatible inputs; a service must enforce the current-time boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ticker: str = Field(min_length=1, max_length=32)
    date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    analysts: list[Analyst] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"],
        min_length=1,
        max_length=4,
    )
    depth: Literal[1, 2, 3] = 1
    language: Language = "Chinese"
    checkpoint: bool | None = None
    template_version_id: str | None = Field(default=None, max_length=36)
    objective: Literal["general", "technical", "earnings", "thematic", "risk_review"] = "general"
    horizon: Literal["short", "medium", "long"] = "medium"
    holding_state: Literal["unknown", "not_held", "held"] = "unknown"
    max_llm_calls: int = Field(default=80, ge=1, le=500)
    max_tokens_total: int | None = Field(default=None, ge=1, le=10_000_000)
    max_runtime_seconds: int = Field(default=1800, ge=60, le=14400)
    context_ids: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("context_ids")
    @classmethod
    def valid_context_ids(cls, values):
        normalized = [str(UUID(value)) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("附加资料不能重复")
        return normalized

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9^][A-Z0-9._^=\-]*", value):
            raise ValueError("请输入有效证券代码")
        return value

    @field_validator("date")
    @classmethod
    def calendar_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value or value < "1970-01-01":
            raise ValueError("日期须为 1970-01-01 之后的 YYYY-MM-DD")
        return value

    @field_validator("depth", mode="before")
    @classmethod
    def integer_depth(cls, value):
        if type(value) is not int:
            raise ValueError("研究深度须为 1、2 或 3")
        return value

    @field_validator("analysts")
    @classmethod
    def unique_analysts(cls, value: list[Analyst]) -> list[Analyst]:
        if len(set(value)) != len(value):
            raise ValueError("分析师不可重复")
        return [key for key in ("market", "social", "news", "fundamentals") if key in value]


class RunStats(ContractModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    current_attempt: RunAttemptStats | None = None
    usage_quality: UsageQuality = "unknown"
    usage_reported_calls: int | None = Field(default=None, ge=0)
    uncertain_calls: int = Field(default=0, ge=0)


class LegacyRunEvent(ContractModel):
    id: int = Field(ge=0)
    time: datetime
    type: str
    message: str


class RunSnapshot(ContractModel):
    """Safe shape of the existing run store, including its pending state."""

    id: UUID
    ticker: str
    date: str
    analysts: list[Analyst]
    asset_type: Literal["stock", "crypto"]
    depth: Literal[1, 2, 3]
    language: Language
    checkpoint: bool
    status: RunStatus
    stage: str
    revision: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rating: str | None = None
    raw_rating: str | None = None
    quality: ResearchQualitySummary = Field(
        default_factory=lambda: ResearchQualitySummary(
            status="unverified", evidence_count=0, validated_price=False,
            eligible_for_rating=False, eligible_for_levels=False, eligible_for_sizing=False,
            required_categories=[], available_categories=[], missing_categories=[], issues=[],
        )
    )
    reports: dict[str, str] = Field(default_factory=dict)
    events: list[LegacyRunEvent] = Field(default_factory=list)
    error: str | None = None
    stats: RunStats = Field(default_factory=RunStats)
    report_url: str | None = None
    markdown_url: str | None = None
    objective: Literal["general", "technical", "earnings", "thematic", "risk_review"] | None = None
    horizon: Literal["short", "medium", "long"] | None = None
    holding_state: Literal["unknown", "not_held", "held"] = "unknown"
    max_llm_calls: int | None = None
    max_tokens_total: int | None = None
    max_runtime_seconds: int | None = None


class ProgressPayload(ContractModel):
    stage: str
    label: str


class ReportPayload(ContractModel):
    section: str
    content: str


class StatePayload(ContractModel):
    status: RunStatus
    stage: str


class ResyncPayload(ContractModel):
    reason: str
    latest_event_id: str | None = None


class EventBase(ContractModel):
    event_id: str
    seq: int = Field(ge=1)
    run_id: UUID
    attempt_id: UUID | None
    timestamp: datetime
    schema_version: Literal["1"] = "1"


class ProgressEvent(EventBase):
    type: Literal["progress"]
    payload: ProgressPayload


class ReportEvent(EventBase):
    type: Literal["report"]
    payload: ReportPayload


class StatsEvent(EventBase):
    type: Literal["stats"]
    payload: RunStats


class StateEvent(EventBase):
    type: Literal["state"]
    payload: StatePayload


class ErrorEvent(EventBase):
    type: Literal["error"]
    payload: ApiError


class ResyncEvent(EventBase):
    type: Literal["resync_required"]
    payload: ResyncPayload


class EventEnvelope(
    RootModel[
        Annotated[
            ProgressEvent | ReportEvent | StatsEvent | StateEvent | ErrorEvent | ResyncEvent,
            Field(discriminator="type"),
        ]
    ]
):
    """Stored event data; SSE framing and reconnection remain a separate transport."""


CONTRACT_MODELS = (CreateResearchRequest, RunSnapshot, EventEnvelope, ApiError)
