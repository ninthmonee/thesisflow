"""Versioned external-agent research contracts independent of graph roles."""

from typing import Literal

from pydantic import Field

from tradingagents.web.api.base import ContractModel

POLICY_VERSION = "local-agent-evidence-v1"
EXECUTOR_VERSION = "codex-research-v1"


def evidence_policy(objective):
    required = ["market"] if objective == "technical" else ["market", "fundamentals", "news"]
    return {"version": POLICY_VERSION, "required_categories": required, "claims": "unverified"}


class LocalAgentReport(ContractModel):
    ticker: str = Field(min_length=1, max_length=32)
    analysis_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    summary: str = Field(min_length=1, max_length=6000)
    analysis: str = Field(min_length=1, max_length=20000)
    bull_case: str = Field(min_length=1, max_length=8000)
    bear_case: str = Field(min_length=1, max_length=8000)
    risks: str = Field(min_length=1, max_length=8000)
    conclusion: str = Field(min_length=1, max_length=8000)
    evidence_refs: list[str] = Field(max_length=100)
    source_coverage: Literal["complete", "partial", "unavailable"]
