"""Owned data connections store only opaque OS-keyring references."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, Versioned


class AlternativeDataConnection(Versioned, Base):
    __tablename__ = "market_data_connections"
    __table_args__ = (
        CheckConstraint("provider IN ('massive', 'intrinio', 'alpaca')", name="provider"),
        CheckConstraint("revision >= 1", name="revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40))
    feed: Mapped[str] = mapped_column(String(40), default="indicative")
    credential_ref: Mapped[str] = mapped_column(String(240))
    secondary_credential_ref: Mapped[str | None] = mapped_column(String(240))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    test_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
