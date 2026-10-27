"""Owner-scoped queue observations over independent worker process heartbeats."""

from datetime import UTC, datetime

from sqlalchemy import select

from tradingagents.web.api.readiness_schemas import (
    QueueReadiness,
    RuntimeReadiness,
    WorkerReadiness,
)
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.models import Membership, ResearchRun, RunAttempt
from tradingagents.web.infrastructure.worker_health_model import WorkerHeartbeatRecord


class RuntimeReadinessService:
    def __init__(self, database, local_workspace, *, clock=None):
        self.database, self.workspace = database, local_workspace
        self.clock = clock or (lambda: datetime.now(UTC))

    def get_readiness(self):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Readiness clock must have timezone")
        now = now.astimezone(UTC)
        with self.database.session() as session:
            role = session.scalar(
                select(Membership.role).where(
                    Membership.workspace_id == self.workspace.workspace_id,
                    Membership.user_id == self.workspace.user_id,
                )
            )
            if role is None:
                raise ResearchError("readiness_forbidden", "当前用户无权读取此工作区运行状态", 403)
            rows = session.scalars(select(WorkerHeartbeatRecord)).all()
            online = [
                row
                for row in rows
                if row.state in {"idle", "busy"} and row.heartbeat_at <= now < row.expires_at
            ]
            runs = session.scalars(
                select(ResearchRun).where(
                    ResearchRun.workspace_id == self.workspace.workspace_id,
                    ResearchRun.owner_id == self.workspace.user_id,
                    ResearchRun.status.in_(("queued", "running", "cancelling")),
                )
            ).all()
            owned = {run.id for run in runs}
            active_run = next(
                (
                    row.current_run_id
                    for row in sorted(online, key=lambda row: row.heartbeat_at, reverse=True)
                    if row.current_run_id in owned
                ),
                None,
            )
            latest_attempts = {}
            if owned:
                for attempt in session.scalars(
                    select(RunAttempt)
                    .where(RunAttempt.run_id.in_(owned))
                    .order_by(RunAttempt.attempt_no.desc())
                ):
                    latest_attempts.setdefault(attempt.run_id, attempt)
            stalled = sum(
                run.status in {"running", "cancelling"}
                and (run.id not in latest_attempts or latest_attempts[run.id].lease_until <= now)
                for run in runs
            )
            idle = sum(row.state == "idle" for row in online)
            queued = [run for run in runs if run.status == "queued"]
            worker = WorkerReadiness(
                state="ready" if idle else "busy" if online else "offline",
                active_workers=len(online),
                idle_workers=idle,
                busy_workers=len(online) - idle,
                last_heartbeat_at=max(
                    (row.heartbeat_at for row in rows if row.heartbeat_at <= now), default=None
                ),
                current_run_id=active_run,
            )
            queue = QueueReadiness(
                queued_count=len(queued),
                running_count=sum(run.status == "running" for run in runs),
                cancelling_count=sum(run.status == "cancelling" for run in runs),
                stalled_count=stalled,
                oldest_queued_at=min((run.created_at for run in queued), default=None),
            )
            return RuntimeReadiness(
                status="ready" if online and not stalled else "degraded",
                checked_at=now,
                worker=worker,
                queue=queue,
            )
