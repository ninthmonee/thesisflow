"""Agent runtime metadata stores no credentials or global CLI configuration."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, Versioned, new_id


class AgentRuntimeConnection(Versioned, Base):
    __tablename__ = "agent_runtime_connections"
    __table_args__ = (
        CheckConstraint("runtime_type = 'codex'", name="runtime_type"),
        CheckConstraint("auth_mode IN ('runtime_login','model_connection')", name="auth_mode"),
        CheckConstraint("revision >= 1", name="revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    runtime_type: Mapped[str] = mapped_column(String(32), default="codex")
    executable_path: Mapped[str | None] = mapped_column(String(1000))
    model_id: Mapped[str] = mapped_column(String(240))
    reasoning_effort: Mapped[str | None] = mapped_column(String(24))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_mode: Mapped[str] = mapped_column(String(32), default="runtime_login")
    model_profile_id: Mapped[str | None] = mapped_column(ForeignKey("model_profiles.id", ondelete="SET NULL"))
    readiness: Mapped[str] = mapped_column(String(24), default="not_checked")
    probe_result: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
