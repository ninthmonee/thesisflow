"""Explicit, server-frozen supplemental research inputs; never arbitrary client evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .base import ContractModel


class ResearchContextCreate(ContractModel):
    kind: Literal["market", "theme", "options", "news", "etf_flows"]
    symbol: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9.\-]*$")
    market_snapshot_id: str | None = Field(default=None, pattern=r"^mkt-[a-f0-9]{32}$")
    theme_id: UUID | None = None
    theme_version: int | None = Field(default=None, ge=1)
    provider: Literal["yahoo", "ishares", "massive", "alpaca", "intrinio"] | None = None
    connection_id: UUID | None = None
    expiration: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    lookback_days: int = Field(default=30, ge=1, le=365)
    lookback_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("expiration")
    @classmethod
    def valid_date(cls, value):
        if value:
            from datetime import date
            date.fromisoformat(value)
        return value


class ResearchContextView(ContractModel):
    id: str
    kind: str
    symbol: str | None
    source: str
    as_of: str | None
    received_at: datetime
    content_hash: str
    summary: str
    scope_notice: str


class ResearchContextList(ContractModel):
    items: list[ResearchContextView]
