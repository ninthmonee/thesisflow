"""Private instrument observation metadata, shared across an owner's watchlist groups."""

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Versioned


class WatchlistObservationRecord(Versioned, Base):
    __tablename__ = "watchlist_observations"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("length(notes) <= 2000", name="notes_length"),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    favorite: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
