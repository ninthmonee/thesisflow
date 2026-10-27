"""Process liveness references; internal worker names never enter public responses."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime


class WorkerHeartbeatRecord(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (CheckConstraint("state IN ('idle','busy','stopped')", name="state"),)
    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    instance_token: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    current_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
