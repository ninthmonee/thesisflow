"""Persistent research commands and event cursors, without external calls."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tradingagents.web.api.schemas import EventEnvelope, RunSnapshot
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.tasks import LeaseLost, ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import (
    Instrument,
    Membership,
    Report,
    ResearchRun,
    Workspace,
)


@pytest.fixture
def research(tmp_path):
    database = initialize_database(tmp_path / "app.sqlite3")
    local = database.local_workspace()
    clock = [datetime.now(UTC)]
    service = ResearchService(database, tmp_path / "artifacts", clock=lambda: clock[0])
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    yield service, scope, clock
    database.dispose()


def create(service, scope, **fields):
    return service.create_run({"ticker": "NVDA", "date": "2024-01-02", **fields}, **scope)


def test_creation_is_persistent_and_never_executes(research, tmp_path):
    service, scope, _clock = research
    run = create(service, scope)
    assert run["status"] == "queued"
    RunSnapshot.model_validate(run)
    assert not service.artifacts_root.exists()
    reopened = initialize_database(tmp_path / "app.sqlite3")
    try:
        second = ResearchService(reopened, tmp_path / "artifacts")
        assert second.get_run(run["id"], **scope)["status"] == "queued"
    finally:
        reopened.dispose()


def test_known_funds_and_indices_do_not_use_company_research(research):
    service, scope, _clock = research
    with service.database.session() as session:
        session.add(Instrument(symbol="TESTFUND", name="Catalog fund", asset_type="etf"))
    for ticker in ("SPY", "XLK", "^GSPC", "TESTFUND"):
        with pytest.raises(ResearchError) as error:
            create(service, scope, ticker=ticker)
        assert error.value.code == "research_asset_not_supported"
    assert service.list_runs(**scope)["items"] == []


def test_idempotent_submission_and_conflicting_payload(research):
    service, scope, _clock = research
    first = service.create_run(
        {"ticker": "NVDA", "date": "2024-01-02"}, idempotency_key="submit", **scope
    )
    same = service.create_run(
        {"ticker": "NVDA", "date": "2024-01-02"}, idempotency_key="submit", **scope
    )
    assert first["id"] == same["id"]
    with pytest.raises(ResearchError, match="相同幂等键"):
        service.create_run(
            {"ticker": "AAPL", "date": "2024-01-02"}, idempotency_key="submit", **scope
        )
    assert len(service.list_runs(**scope)["items"]) == 1


def test_scope_is_checked_for_reads_events_and_mutations(research):
    service, scope, _clock = research
    run = create(service, scope)
    with service.database.session() as session:
        session.add(Workspace(id="other", owner_id=scope["owner_id"], name="other"))
        session.flush()
        session.add(Membership(workspace_id="other", user_id=scope["owner_id"], role="owner"))
    other = {**scope, "workspace_id": "other"}
    for method in (
        service.get_run,
        service.get_manifest,
        service.cancel_run,
        service.resume_run,
        service.list_events,
    ):
        with pytest.raises(ResearchError) as captured:
            method(run["id"], **other)
        assert captured.value.status_code == 404
    assert service.list_runs(**other)["items"] == []


def test_manifest_rejects_credentials_and_freezes_runtime_configuration(research):
    service, scope, _clock = research
    configuration = {
        "quick_think_llm": "first-model",
        "api_key": "do-not-store",
        "results_dir": "/private/path",
    }
    service.config_snapshot_provider = lambda: configuration
    run = create(service, scope)
    configuration["quick_think_llm"] = "changed-model"
    manifest = service.get_manifest(run["id"], **scope)
    assert manifest["runtime"] == {"quick_think_llm": "first-model"}
    assert manifest["workspace_id"] == scope["workspace_id"]
    with pytest.raises(ResearchError, match="不能保存凭据"):
        service.create_run(
            {"ticker": "NVDA", "date": "2024-01-02"},
            manifest={"nested": {"api_key": "secret"}},
            **scope,
        )


def test_keyset_pagination_has_no_duplicates_after_new_insert(research):
    service, scope, clock = research
    ids = []
    for _ in range(3):
        ids.append(create(service, scope)["id"])
        clock[0] += timedelta(seconds=1)
    first = service.list_runs(limit=2, **scope)
    create(service, scope, ticker="AAPL")
    second = service.list_runs(limit=2, cursor=first["next_cursor"], **scope)
    assert {row["id"] for row in first["items"] + second["items"]} == set(ids)
    assert second["next_cursor"] is None
    assert all(row["reports"] == {} and row["events"] == [] for row in first["items"])


def test_queue_cancel_does_not_claim_or_call_any_model(research):
    service, scope, _clock = research
    run = create(service, scope)
    assert service.cancel_run(run["id"], **scope)["status"] == "cancelled"
    assert service.tasks.claim_next("worker") is None
    assert service.cancel_run(run["id"], **scope)["status"] == "cancelled"


def test_viewer_cannot_start_research(research):
    service, scope, _clock = research
    with service.database.session() as session:
        membership = session.scalar(
            select(Membership).where(Membership.workspace_id == scope["workspace_id"])
        )
        membership.role = "viewer"
    with pytest.raises(ResearchError) as captured:
        create(service, scope)
    assert captured.value.status_code == 403


def test_expired_worker_cannot_publish_or_finish(research):
    service, scope, clock = research
    run = create(service, scope)
    claim = service.tasks.claim_next("old-worker", lease_seconds=10)
    clock[0] += timedelta(seconds=11)
    assert service.tasks.reap_expired() == 1
    assert service.get_run(run["id"], **scope)["status"] == "interrupted"
    for action in (
        lambda: service.tasks.publish(claim, reports={"market_report": "stale"}),
        lambda: service.tasks.finish(claim, "completed"),
        lambda: service.tasks.heartbeat(claim),
    ):
        with pytest.raises(LeaseLost):
            action()
    assert service.get_run(run["id"], **scope)["reports"] == {}


def test_events_are_durable_ordered_and_paginated(research):
    service, scope, _clock = research
    run = create(service, scope)
    claim = service.tasks.claim_next("worker")
    service.tasks.publish(claim, stage="analysts", reports={"market_report": "evidence"})
    first = service.list_events(run["id"], limit=2, **scope)
    second = service.list_events(run["id"], after_seq=first[-1]["seq"], **scope)
    events = first + second
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["event_id"] for event in events}) == len(events)
    for event in events:
        EventEnvelope.model_validate(event)
    assert events[0]["attempt_id"] is None
    assert events[-1]["payload"] == {"section": "market_report", "content": "evidence"}


def test_report_file_cannot_escape_its_run_directory(research, tmp_path):
    service, scope, _clock = research
    run = create(service, scope)
    outsider = tmp_path / "private.html"
    outsider.write_text("private")
    with service.database.session() as session:
        session.add(
            Report(
                workspace_id=scope["workspace_id"],
                run_id=run["id"],
                kind="_export_html",
                version=1,
                file_path=str(outsider),
                content_hash="0" * 64,
            )
        )
    with pytest.raises(ResearchError) as captured:
        service.report_file(run["id"], **scope)
    assert captured.value.status_code == 404


def test_resume_without_checkpoint_is_explicitly_unavailable(research):
    service, scope, clock = research
    run = create(service, scope)
    service.tasks.claim_next("worker", lease_seconds=5)
    clock[0] += timedelta(seconds=6)
    service.tasks.reap_expired()
    with pytest.raises(ResearchError) as captured:
        service.resume_run(run["id"], **scope)
    assert captured.value.code == "checkpoint_unavailable"


def test_run_status_is_not_marked_complete_by_partial_reports(research):
    service, scope, _clock = research
    run = create(service, scope)
    claim = service.tasks.claim_next("worker")
    service.tasks.publish(claim, reports={"market_report": "partial"})
    with service.database.session() as session:
        stored = session.get(ResearchRun, run["id"])
        assert stored.status == "running"
    assert service.get_run(run["id"], **scope)["rating"] is None
