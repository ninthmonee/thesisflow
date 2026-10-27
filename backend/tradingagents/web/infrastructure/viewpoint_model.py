"""Manually confirmed research views, owned independently of engine memory."""

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, Versioned


class ResearchViewpoint(Versioned, Base):
    __tablename__ = "research_viewpoints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_id", "run_id"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("status IN ('active','invalidated','archived')", name="status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    ticker: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(20))
    thesis: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    review_on: Mapped[str | None] = mapped_column(String(10))
    review_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    confirmed_at: Mapped[object] = mapped_column(UTCDateTime())
    reviewed_at: Mapped[object | None] = mapped_column(UTCDateTime())
