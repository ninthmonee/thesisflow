"""Workspace market-runtime preferences, added after the initial frozen schema."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Versioned


class DataSettingsRecord(Versioned, Base):
    __tablename__ = "data_settings"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision"),)

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
