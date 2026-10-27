"""Worker readiness survives API reads, expires honestly, and never exposes other owners' jobs."""

from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from tradingagents.web.api.readiness_router import create_readiness_router
from tradingagents.web.application.runtime_readiness import RuntimeReadinessService
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import (
    Database,
    initialize_database,
    migration_config,
)
from tradingagents.web.infrastructure.models import ResearchRun, RunAttempt, User
from tradingagents.web.infrastructure.worker_health import WorkerHeartbeatStore


@pytest.fixture
def runtime(tmp_path):
    database = initialize_database(tmp_path / "health.sqlite3")
    workspace = database.local_workspace()
    clock = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    store = WorkerHeartbeatStore(database, clock=lambda: clock[0])
    service = RuntimeReadinessService(database, workspace, clock=lambda: clock[0])
    yield database, workspace, store, service, clock
    database.dispose()


def test_worker_liveness_is_independent_of_api_and_token_fenced(runtime):
    _db, _workspace, store, service, clock = runtime
    initial = service.get_readiness()
    assert (
        initial.api == "ready"
        and initial.status == "degraded"
        and initial.worker.state == "offline"
    )
    first = store.register("private-user@private-host")
    assert service.get_readiness().worker.state == "ready"
    replacement = store.register("private-user@private-host")
    assert not store.heartbeat("private-user@private-host", first)
    assert not store.stop("private-user@private-host", first)
    assert store.heartbeat("private-user@private-host", replacement)
    public = service.get_readiness().model_dump_json()
    assert (
        "private-user" not in public and "private-host" not in public and replacement not in public
    )
    clock[0] += timedelta(seconds=31)
    assert service.get_readiness().worker.state == "offline"
    assert service.get_readiness().worker.last_heartbeat_at is not None
    store.heartbeat("private-user@private-host", replacement)
    assert store.stop("private-user@private-host", replacement)
    assert service.get_readiness().worker.state == "offline"


def test_queue_and_busy_run_are_scoped_and_expired_leases_are_observed_without_rewriting(runtime):
    database, workspace, store, service, clock = runtime
    with database.session() as session:
        for identifier, status in (("queued-owned", "queued"), ("running-owned", "running")):
            session.add(
                ResearchRun(
                    id=identifier,
                    workspace_id=workspace.workspace_id,
                    owner_id=workspace.user_id,
                    ticker="NVDA",
                    analysis_date=date(2026, 10, 23),
                    status=status,
                    request_hash=identifier,
                    created_at=clock[0],
                )
            )
        session.flush()
        session.add(
            RunAttempt(
                id="expired-attempt",
                workspace_id=workspace.workspace_id,
                run_id="running-owned",
                attempt_no=1,
                lease_owner="worker",
                lease_token=1,
                lease_until=clock[0] - timedelta(seconds=1),
                heartbeat_at=clock[0] - timedelta(seconds=60),
                created_at=clock[0] - timedelta(seconds=60),
            )
        )
    token = store.register("worker")
    assert store.heartbeat("worker", token, state="busy", run_id="running-owned")
    result = service.get_readiness()
    assert result.worker.state == "busy" and result.worker.current_run_id == "running-owned"
    assert (
        result.queue.queued_count == result.queue.running_count == result.queue.stalled_count == 1
    )
    assert result.status == "degraded"
    with database.session() as session:
        assert session.get(ResearchRun, "running-owned").status == "running"
    from tradingagents.web.infrastructure.seeds import LocalWorkspace

    outsider = RuntimeReadinessService(
        database,
        LocalWorkspace(
            user_id="unknown", workspace_id=workspace.workspace_id, watchlist_id="", name="unknown"
        ),
    )
    with pytest.raises(ResearchError) as forbidden:
        outsider.get_readiness()
    assert forbidden.value.status_code == 403


def test_readiness_route_is_read_only_and_requires_runtime_reader(runtime):
    _db, _workspace, _store, service, _clock = runtime
    app = FastAPI()
    app.include_router(create_readiness_router(lambda: service))
    with TestClient(app) as client:
        result = client.get("/api/v1/readiness")
        assert result.status_code == 200 and result.json()["api"] == "ready"
        assert result.json()["worker"]["state"] == "offline"
        assert client.post("/api/v1/readiness").status_code == 405
    unavailable = FastAPI()
    unavailable.include_router(create_readiness_router())
    with TestClient(unavailable) as client:
        assert client.get("/api/v1/readiness").status_code == 503


def test_global_worker_capacity_never_discloses_other_owners_run_or_queue(runtime):
    database, workspace, store, service, clock = runtime
    with database.session() as session:
        session.add(User(id="another-owner", name="another owner"))
        session.flush()
        for identifier, status in (("private-queued", "queued"), ("private-running", "running")):
            session.add(
                ResearchRun(
                    id=identifier,
                    workspace_id=workspace.workspace_id,
                    owner_id="another-owner",
                    ticker="AAPL",
                    analysis_date=date(2026, 10, 23),
                    status=status,
                    request_hash=identifier,
                    created_at=clock[0],
                )
            )
    token = store.register("shared-worker")
    store.heartbeat("shared-worker", token, state="busy", run_id="private-running")
    result = service.get_readiness()
    assert result.worker.active_workers == result.worker.busy_workers == 1
    assert result.worker.current_run_id is None
    assert (
        result.queue.queued_count == result.queue.running_count == result.queue.stalled_count == 0
    )
    assert "private-" not in result.model_dump_json()


def test_heartbeat_migration_is_additive_and_idle_constructors_have_no_side_effect(tmp_path):
    database = Database(tmp_path / "legacy.sqlite3")
    try:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), "0008_watchlist_observations")
        local = database.local_workspace()
        database.migrate()
        with database.engine.connect() as connection:
            assert "worker_heartbeats" in inspect(connection).get_table_names()
        service = RuntimeReadinessService(database, local)
        assert service.get_readiness().worker.active_workers == 0
    finally:
        database.dispose()
    untouched = Database(tmp_path / "missing" / "not-created.sqlite3")
    try:
        WorkerHeartbeatStore(untouched)
        assert not untouched.path.parent.exists()
    finally:
        untouched.dispose()
