"""Research connection records contain opaque Keychain references only."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, Versioned


class ResearchDataConnectionRecord(Versioned, Base):
    __tablename__ = "research_data_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_id", "provider"),
        CheckConstraint(
            "provider IN ('fred','alpha_vantage','typesafe_jev','sec_edgar')", name="provider"
        ),
        CheckConstraint("credential_source IN ('keychain','legacy_environment')", name="source"),
        CheckConstraint("revision >= 1", name="revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    credential_source: Mapped[str] = mapped_column(String(40), default="keychain")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    credential_ref: Mapped[str | None] = mapped_column(String(240))
    user_agent_ref: Mapped[str | None] = mapped_column(String(240))
    model: Mapped[str | None] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(String(240))
    test_status: Mapped[str] = mapped_column(String(16), default="untested")
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
