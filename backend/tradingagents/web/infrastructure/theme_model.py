"""Immutable thematic configurations and scoped current-version pointers."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Created, UTCDateTime, Versioned, new_id, utc_now


class ThemeRecord(Versioned, Base):
    __tablename__ = "themes"
    __table_args__ = (UniqueConstraint("workspace_id", "owner_id", "builtin_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    builtin_key: Mapped[str | None] = mapped_column(String(80))
    current_version: Mapped[int] = mapped_column(default=1)
    archived: Mapped[bool] = mapped_column(default=False)
    pinned: Mapped[bool] = mapped_column(default=False, server_default=false())
    pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ThemeVersionRecord(Created, Base):
    __tablename__ = "theme_versions"
    __table_args__ = (UniqueConstraint("theme_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), index=True)
    version: Mapped[int]
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    effective_from: Mapped[Any] = mapped_column(UTCDateTime(), default=utc_now)


class ThemeMemberRecord(Base):
    __tablename__ = "theme_members"
    __table_args__ = (UniqueConstraint("version_id", "symbol"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("theme_versions.id"), index=True)
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("instruments.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    position: Mapped[int]
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
