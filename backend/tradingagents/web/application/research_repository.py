"""SQLite-backed task commands with fenced, short transactions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tradingagents.web.domain.tasks import LeaseLost, ResearchError, TaskClaim
from tradingagents.web.infrastructure.database import Database
from tradingagents.web.infrastructure.models import (
    Report,
    ResearchRun,
    RunAttempt,
    RunEvent,
    RunManifest,
)

TERMINAL = {"cancelled", "completed", "failed", "interrupted"}
STAGES = {
    "initializing": 0,
    "analysts": 1,
    "research": 2,
    "trading": 3,
    "risk": 4,
    "portfolio": 5,
    "completed": 6,
}
STAGE_LABELS = {
    "initializing": "正在初始化模型与标的信息",
    "analysts": "分析师正在并行研究",
    "research": "研究团队正在进行多空辩论",
    "trading": "正在制定交易计划",
    "risk": "风险团队正在评估交易建议",
    "portfolio": "组合经理正在形成最终决策",
    "completed": "分析完成",
}


class TaskRepository:
    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("worker clock must return timezone-aware UTC timestamps")
        return value.astimezone(UTC)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.database.session() as session:
            # SQLite serializes command decisions, not model/network calls.
            session.execute(text("BEGIN IMMEDIATE"))
            yield session

    @staticmethod
    def scoped_run(session: Session, run_id: str, workspace_id: str, owner_id: str) -> ResearchRun:
        run = session.scalar(
            select(ResearchRun).where(
                ResearchRun.id == run_id,
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.owner_id == owner_id,
            )
        )
        if run is None:
            raise ResearchError("run_not_found", "任务不存在", 404)
        return run

    @staticmethod
    def latest_attempt(session: Session, run_id: str) -> RunAttempt | None:
        return session.scalar(
            select(RunAttempt)
            .where(RunAttempt.run_id == run_id)
            .order_by(RunAttempt.attempt_no.desc())
            .limit(1)
        )

    def append_event(
        self,
        session: Session,
        run: ResearchRun,
        event_type: str,
        payload: dict[str, Any],
        attempt_id: str | None = None,
    ) -> None:
        sequence = run.next_event_seq
        run.next_event_seq += 1
        run.revision += 1
        run.updated_at = self.now()
        session.add(
            RunEvent(
                workspace_id=run.workspace_id,
                run_id=run.id,
                attempt_id=attempt_id,
                event_seq=sequence,
                schema_version="1",
                event_type=event_type,
                payload=payload,
                occurred_at=self.now(),
            )
        )

    def _fenced(self, session: Session, claim: TaskClaim) -> tuple[ResearchRun, RunAttempt]:
        attempt = session.scalar(
            select(RunAttempt).where(
                RunAttempt.id == claim.attempt_id,
                RunAttempt.run_id == claim.run_id,
                RunAttempt.workspace_id == claim.workspace_id,
                RunAttempt.lease_owner == claim.lease_owner,
                RunAttempt.lease_token == claim.lease_token,
                RunAttempt.lease_until > self.now(),
                RunAttempt.status == "running",
            )
        )
        if attempt is None:
            raise LeaseLost()
        run = self.scoped_run(session, claim.run_id, claim.workspace_id, claim.owner_id)
        latest = self.latest_attempt(session, run.id)
        if (
            latest is None
            or latest.id != claim.attempt_id
            or run.status not in {"running", "cancelling"}
        ):
            raise LeaseLost()
        return run, attempt

    def claim_next(self, worker_id: str, lease_seconds: float = 90) -> TaskClaim | None:
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("worker_id and a lease between 1 and 3600 seconds are required")
        self.reap_expired()
        with self.transaction() as session:
            run = session.scalar(
                select(ResearchRun)
                .where(ResearchRun.status == "queued")
                .order_by(ResearchRun.created_at, ResearchRun.id)
                .limit(1)
            )
            if run is None:
                return None
            previous = self.latest_attempt(session, run.id)
            attempt_no = previous.attempt_no + 1 if previous else 1
            now = self.now()
            attempt = RunAttempt(
                id=str(uuid4()),
                workspace_id=run.workspace_id,
                run_id=run.id,
                attempt_no=attempt_no,
                lease_owner=worker_id,
                lease_token=attempt_no,
                lease_until=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
                checkpoint_ref=previous.checkpoint_ref if previous else None,
                created_at=now,
            )
            session.add(attempt)
            session.flush()
            run.status = "running"
            run.stage = "initializing"
            run.started_at = run.started_at or now
            run.finished_at = None
            run.error_code = None
            run.error_message = None
            self.append_event(
                session, run, "state", {"status": "running", "stage": run.stage}, attempt.id
            )
            return TaskClaim(
                run.id,
                run.workspace_id,
                run.owner_id,
                attempt.id,
                attempt.attempt_no,
                worker_id,
                attempt.lease_token,
                attempt.lease_until,
                attempt.checkpoint_ref,
            )

    def reap_expired(self) -> int:
        count = 0
        with self.transaction() as session:
            expired = session.scalars(
                select(RunAttempt).where(
                    RunAttempt.status == "running",
                    RunAttempt.lease_until <= self.now(),
                )
            ).all()
            for attempt in expired:
                run = session.get(ResearchRun, attempt.run_id)
                attempt.status = "interrupted"
                attempt.finished_at = self.now()
                if run is not None and run.status in {"running", "cancelling"}:
                    run.status = "interrupted"
                    run.finished_at = self.now()
                    run.error_code = "worker_interrupted"
                    run.error_message = "执行进程失联，已保存阶段结果；外部调用可能已发生"
                    self.append_event(
                        session,
                        run,
                        "state",
                        {"status": run.status, "stage": run.stage},
                        attempt.id,
                    )
                    count += 1
        return count

    def heartbeat(self, claim: TaskClaim, lease_seconds: float = 90) -> str:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease must be between 1 and 3600 seconds")
        with self.transaction() as session:
            run, attempt = self._fenced(session, claim)
            attempt.heartbeat_at = self.now()
            attempt.lease_until = self.now() + timedelta(seconds=lease_seconds)
            return run.status

    def active_status(self, claim: TaskClaim) -> str:
        with self.database.session() as session:
            run, _attempt = self._fenced(session, claim)
            return run.status

    def manifest(self, claim: TaskClaim) -> dict[str, Any]:
        with self.database.session() as session:
            self._fenced(session, claim)
            manifest = session.get(RunManifest, claim.run_id)
            if manifest is None:
                raise ResearchError("manifest_missing", "研究配置清单缺失")
            return {"content": manifest.content, "content_hash": manifest.content_hash}

    def publish(
        self,
        claim: TaskClaim,
        *,
        stage: str | None = None,
        stats: dict[str, Any] | None = None,
        reports: dict[str, str] | None = None,
        checkpoint_ref: str | None = None,
        rating: str | None = None,
    ) -> None:
        import hashlib

        with self.transaction() as session:
            run, attempt = self._fenced(session, claim)
            if stage is not None and STAGES.get(stage, -1) > STAGES.get(run.stage, -1):
                run.stage = stage
                self.append_event(
                    session,
                    run,
                    "progress",
                    {"stage": stage, "label": STAGE_LABELS[stage]},
                    attempt.id,
                )
            if stats is not None:
                self.append_event(session, run, "stats", dict(stats), attempt.id)
            for kind, content in (reports or {}).items():
                previous = session.scalar(
                    select(Report)
                    .where(Report.run_id == run.id, Report.kind == kind)
                    .order_by(Report.version.desc())
                    .limit(1)
                )
                digest = hashlib.sha256(content.encode()).hexdigest()
                if previous is not None and previous.content_hash == digest:
                    continue
                session.add(
                    Report(
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        kind=kind,
                        version=previous.version + 1 if previous else 1,
                        content=content,
                        content_hash=digest,
                    )
                )
                self.append_event(
                    session, run, "report", {"section": kind, "content": content}, attempt.id
                )
            if checkpoint_ref is not None:
                attempt.checkpoint_ref = checkpoint_ref
            if rating is not None:
                previous_rating = session.scalar(
                    select(Report)
                    .where(Report.run_id == run.id, Report.kind == "_rating")
                    .order_by(Report.version.desc())
                    .limit(1)
                )
                if previous_rating is None or previous_rating.content != rating:
                    session.add(
                        Report(
                            workspace_id=run.workspace_id,
                            run_id=run.id,
                            kind="_rating",
                            version=previous_rating.version + 1 if previous_rating else 1,
                            content=rating,
                            content_hash=hashlib.sha256(rating.encode()).hexdigest(),
                        )
                    )

    def register_export(self, claim: TaskClaim, kind: str, path: str, content_hash: str) -> None:
        with self.transaction() as session:
            run, _attempt = self._fenced(session, claim)
            previous = session.scalar(
                select(Report)
                .where(Report.run_id == run.id, Report.kind == kind)
                .order_by(Report.version.desc())
                .limit(1)
            )
            session.add(
                Report(
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    kind=kind,
                    version=previous.version + 1 if previous else 1,
                    file_path=path,
                    content_hash=content_hash,
                )
            )

    def finish(
        self,
        claim: TaskClaim,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid worker terminal status")
        with self.transaction() as session:
            run, attempt = self._fenced(session, claim)
            if run.status == "cancelling":
                status, error_code, error_message = "cancelled", None, None
            run.status = status
            run.stage = "completed" if status == "completed" else run.stage
            run.finished_at = self.now()
            run.error_code, run.error_message = error_code, error_message
            attempt.status, attempt.finished_at = status, self.now()
            if stats is not None:
                self.append_event(session, run, "stats", dict(stats), attempt.id)
            if error_code is not None:
                self.append_event(
                    session,
                    run,
                    "error",
                    {
                        "error_code": error_code,
                        "message": error_message or "研究执行失败",
                        "retryable": False,
                        "trace_id": None,
                    },
                    attempt.id,
                )
            self.append_event(
                session, run, "state", {"status": run.status, "stage": run.stage}, attempt.id
            )
