"""Cumulative usage preserves attempt boundaries and never fabricates missing usage."""

import sys
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from uuid import uuid4

import pytest

from tradingagents.web.api.research_evidence_schemas import ResearchQualitySummary
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Report, ResearchRun, RunAttempt, RunManifest


@pytest.fixture
def usage(tmp_path):
    database = initialize_database(tmp_path / "usage.sqlite3")
    local = database.local_workspace()
    clock = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    service = ResearchService(database, tmp_path / "artifacts", clock=lambda: clock[0])
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    with database.session() as session:
        session.add(
            ResearchRun(
                id="usage-run",
                **scope,
                ticker="NVDA",
                analysis_date=date(2026, 10, 23),
                status="running",
                request_hash="test",
                created_at=clock[0],
            )
        )
    yield service, scope, clock
    database.dispose()


def attempt(session, scope, number, started, finished=None):
    row = RunAttempt(
        id=f"attempt-{number}",
        workspace_id=scope["workspace_id"],
        run_id="usage-run",
        attempt_no=number,
        lease_owner="worker",
        lease_token=number,
        lease_until=started + timedelta(seconds=90),
        heartbeat_at=started,
        created_at=started,
        status="completed" if finished else "running",
        finished_at=finished,
    )
    session.add(row)
    session.flush()
    return row


def payload(calls, tokens, elapsed):
    return {
        "llm_calls": calls,
        "tool_calls": calls,
        "tokens_in": tokens,
        "tokens_out": tokens // 2,
        "elapsed_seconds": elapsed,
        "usage_reported_calls": calls,
        "uncertain_calls": 0,
        "usage_quality": "complete",
    }


def test_latest_per_attempt_is_summed_once_and_queue_wait_is_not_execution_time(usage):
    service, scope, clock = usage
    beginning = clock[0]
    with service.database.session() as session:
        run = session.get(ResearchRun, "usage-run")
        first = attempt(session, scope, 1, beginning, beginning + timedelta(seconds=10))
        service.tasks.append_event(session, run, "stats", payload(2, 200, 5), first.id)
        service.tasks.append_event(session, run, "stats", payload(4, 400, 10), first.id)
        second = attempt(session, scope, 2, beginning + timedelta(seconds=20))
        service.tasks.append_event(session, run, "stats", payload(1, 100, 4), second.id)
    clock[0] += timedelta(seconds=25)
    total = service.usage_totals("usage-run", **scope)
    assert total["llm_calls"] == 5 and total["tokens_in"] == 500 and total["tokens_out"] == 250
    assert total["elapsed_seconds"] == 15 and total["attempt_count"] == 2
    assert total["usage_reported_calls"] == 5 and total["usage_quality"] == "complete"
    assert total["current_attempt"]["attempt_id"] == "attempt-2"
    assert total["current_attempt"]["llm_calls"] == 1
    prior = service.usage_totals("usage-run", **scope, exclude_attempt_id="attempt-2")
    assert (
        prior["llm_calls"] == 4 and prior["elapsed_seconds"] == 10 and prior["attempt_count"] == 1
    )


def test_legacy_missing_usage_is_unknown_rather_than_known_zero(usage):
    service, scope, clock = usage
    with service.database.session() as session:
        run = session.get(ResearchRun, "usage-run")
        first = attempt(session, scope, 1, clock[0], clock[0] + timedelta(seconds=8))
        service.tasks.append_event(
            session, run, "stats", {"llm_calls": 2, "tokens_in": 0, "tokens_out": 0}, first.id
        )
    total = service.usage_totals("usage-run", **scope)
    assert total["usage_quality"] == "unknown"
    assert total["usage_reported_calls"] is None and total["uncertain_calls"] == 2
    assert total["tokens_in"] == 0


def test_mixed_known_and_uncertain_usage_is_partial_and_legacy_unscoped_event_is_retained(usage):
    service, scope, clock = usage
    with service.database.session() as session:
        run = session.get(ResearchRun, "usage-run")
        service.tasks.append_event(session, run, "stats", {"llm_calls": 3, "tokens_in": 70}, None)
        second = attempt(session, scope, 1, clock[0], clock[0] + timedelta(seconds=4))
        service.tasks.append_event(session, run, "stats", payload(1, 100, 4), second.id)
    total = service.usage_totals("usage-run", **scope)
    assert total["llm_calls"] == 4 and total["tokens_in"] == 170
    assert total["usage_quality"] == "partial" and total["usage_reported_calls"] is None
    assert total["uncertain_calls"] == 3


def test_usage_scope_and_excluded_attempt_must_belong_to_the_same_run(usage):
    service, scope, _clock = usage
    with pytest.raises(ResearchError):
        service.usage_totals("usage-run", workspace_id=scope["workspace_id"], owner_id="outsider")
    with pytest.raises(ResearchError) as absent:
        service.usage_totals("usage-run", **scope, exclude_attempt_id="other-run-attempt")
    assert absent.value.status_code == 404


def test_snapshot_publishes_quality_separately_and_keeps_old_request_fields_unknown(
    usage, monkeypatch
):
    service, scope, _clock = usage
    quality = ResearchQualitySummary(
        status="unverified",
        evidence_count=0,
        validated_price=False,
        eligible_for_rating=False,
        eligible_for_levels=False,
        eligible_for_sizing=False,
        required_categories=["price"],
        available_categories=[],
        missing_categories=["price"],
        issues=[],
    )
    module = ModuleType("tradingagents.web.application.research_evidence")

    class Store:
        def __init__(self, *_args):
            pass

        def get_quality(self, *_args, **_kwargs):
            return quality

    module.ResearchEvidenceStore = Store
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with service.database.session() as session:
        run = session.get(ResearchRun, "usage-run")
        run.status = "completed"
        session.add(
            RunManifest(
                run_id=run.id,
                workspace_id=scope["workspace_id"],
                schema_version="1",
                content={"request": {"analysts": ["market"]}},
                content_hash="test",
            )
        )
        for kind, content in (
            ("final_trade_decision", "Rating: Buy"),
            ("_research_quality", "internal quality record"),
        ):
            session.add(
                Report(
                    workspace_id=scope["workspace_id"],
                    run_id=run.id,
                    kind=kind,
                    version=1,
                    content=content,
                    content_hash=kind,
                )
            )
    result = service.get_run("usage-run", **scope)
    assert result["raw_rating"] == "Buy" and result["rating"] is None
    assert (
        result["quality"]["status"] == "unverified" and "_research_quality" not in result["reports"]
    )
    assert (
        result["objective"]
        is result["horizon"]
        is result["max_llm_calls"]
        is result["max_runtime_seconds"]
        is None
    )
    assert result["holding_state"] == "unknown"
    quality.eligible_for_rating = True
    assert service.get_run("usage-run", **scope)["rating"] == "Buy"


def test_context_inputs_must_exactly_freeze_requested_ids_in_order(usage):
    service, scope, _clock = usage
    first, second = str(uuid4()), str(uuid4())
    packets = [
        {"id": identifier, "hash": "a" * 64, "data": {"symbol": "NVDA"}}
        for identifier in (first, second)
    ]
    request = {"ticker": "NVDA", "date": "2024-01-02", "context_ids": [first, second]}
    for inputs in (None, [], packets[::-1], [packets[0]], [*packets, packets[0]]):
        with pytest.raises(ResearchError) as captured:
            service.create_run(request, manifest={"context_inputs": inputs}, **scope)
        assert captured.value.code == "research_context_unresolved"
    for invalid in (
        {**packets[0], "hash": "not-a-content-hash"},
        {**packets[0], "data": []},
        {"id": first},
    ):
        with pytest.raises(ResearchError) as captured:
            service.create_run(request, manifest={"context_inputs": [invalid, packets[1]]}, **scope)
        assert captured.value.code == "research_context_unresolved"
    with pytest.raises(ResearchError) as captured:
        service.create_run(
            {"ticker": "NVDA", "date": "2024-01-02"},
            manifest={"context_inputs": packets},
            **scope,
        )
    assert captured.value.code == "research_context_unresolved"
    created = service.create_run(request, manifest={"context_inputs": packets}, **scope)
    manifest = service.get_manifest(created["id"], **scope)
    assert manifest["selection"]["context_inputs"] == packets
    packets[0]["data"]["symbol"] = "changed later"
    assert (
        service.get_manifest(created["id"], **scope)["selection"]["context_inputs"][0]["data"][
            "symbol"
        ]
        == "NVDA"
    )
