"""Attempt usage facts; unknown provider usage is distinct from known zero tokens."""

from typing import Literal

from pydantic import Field

from .base import ContractModel

UsageQuality = Literal["complete", "partial", "unknown"]


class RunAttemptStats(ContractModel):
    attempt_id: str | None
    attempt_no: int | None
    status: str
    llm_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    usage_reported_calls: int | None = Field(default=None, ge=0)
    uncertain_calls: int = Field(ge=0)
    usage_quality: UsageQuality
