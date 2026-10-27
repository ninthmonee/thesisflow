"""Short, token-fenced worker heartbeats; constructors perform no I/O."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, text

from .worker_health_model import WorkerHeartbeatRecord


class WorkerHeartbeatStore:
    def __init__(self, database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self):
        stamp = self.clock()
        if stamp.tzinfo is None:
            raise ValueError("Worker heartbeat clock must have timezone")
        return stamp.astimezone(UTC)

    @staticmethod
    def _validate(worker_id, ttl_seconds):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 160:
            raise ValueError("Invalid worker identity")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not 1 <= ttl_seconds <= 3600
        ):
            raise ValueError("Worker heartbeat TTL must be between 1 and 3600 seconds")

    def register(self, worker_id, *, ttl_seconds=30):
        self._validate(worker_id, ttl_seconds)
        stamp, token = self.now(), str(uuid4())
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            # Old offline processes are diagnostic history, not an unlimited registry.
            session.execute(
                delete(WorkerHeartbeatRecord).where(
                    WorkerHeartbeatRecord.expires_at < stamp - timedelta(days=7)
                )
            )
            record = session.get(WorkerHeartbeatRecord, worker_id)
            if record is None:
                record = WorkerHeartbeatRecord(worker_id=worker_id)
                session.add(record)
            record.instance_token, record.state = token, "idle"
            record.started_at = record.heartbeat_at = stamp
            record.expires_at, record.current_run_id = stamp + timedelta(seconds=ttl_seconds), None
        return token

    def heartbeat(self, worker_id, instance_token, *, run_id=None, state="idle", ttl_seconds=30):
        self._validate(worker_id, ttl_seconds)
        if state not in {"idle", "busy"} or (state == "busy") != (run_id is not None):
            raise ValueError("Busy workers must identify their current research run")
        stamp = self.now()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(WorkerHeartbeatRecord, worker_id)
            if record is None or record.instance_token != instance_token:
                return False
            record.state, record.current_run_id = state, run_id
            record.heartbeat_at, record.expires_at = stamp, stamp + timedelta(seconds=ttl_seconds)
            return True

    def stop(self, worker_id, instance_token):
        stamp = self.now()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(WorkerHeartbeatRecord, worker_id)
            if record is None or record.instance_token != instance_token:
                return False
            record.state, record.current_run_id = "stopped", None
            record.heartbeat_at = record.expires_at = stamp
            return True
