"""HTTP/SSE run lifecycle tests against the durable service, without a model call."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.web.api import create_api_router
from tradingagents.web.application.research import ResearchService
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.seeds import LocalWorkspace


@pytest.fixture
def research_api(tmp_path):
    database = initialize_database(tmp_path / "research.sqlite3")
    workspace = database.local_workspace()
    service = ResearchService(
        database,
        tmp_path / "artifacts",
        lambda: {
            "llm_provider": "openai",
            "quick_think_llm": "test-quick",
            "deep_think_llm": "test-deep",
            "api_key": "PRIVATE-NOT-IN-MANIFEST",
        },
    )
    active_scope = [workspace]
    app = FastAPI()
    app.include_router(
        create_api_router(
            research_service_reader=lambda: service, scope_reader=lambda: active_scope[0]
        )
    )
    with TestClient(app) as client:
        yield client, service, workspace, active_scope
    database.dispose()


def start(client, **kwargs):
    return client.post("/api/v1/runs", json={"ticker": "nvda", "date": "2026-01-01"}, **kwargs)


def test_http_create_idempotency_pagination_cancel_and_clone(research_api):
    client, _, _, _ = research_api
    first = start(client, headers={"Idempotency-Key": "request-one"})
    assert first.status_code == 202
    run = first.json()
    assert run["status"] == "queued" and run["ticker"] == "NVDA"
    assert start(client, headers={"Idempotency-Key": "request-one"}).json()["id"] == run["id"]
    conflict = client.post(
        "/api/v1/runs",
        json={"ticker": "AAPL", "date": "2026-01-01"},
        headers={"Idempotency-Key": "request-one"},
    )
    assert conflict.status_code == 409
    assert client.get(f"/api/v1/runs/{run['id']}").json()["status"] == "queued"
    manifest = client.get(f"/api/v1/runs/{run['id']}/manifest")
    assert manifest.status_code == 200 and manifest.json()["selection"]["mode"] == "local"
    assert "PRIVATE-NOT-IN-MANIFEST" not in manifest.text
    cancelled = client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/runs/{run['id']}/resume").status_code == 409
    clone = client.post(f"/api/v1/runs/{run['id']}/clone")
    assert clone.status_code == 422 and clone.json()["error_code"] == "rerun_review_required"
    clone = client.post(f"/api/v1/runs/{run['id']}/clone", json={"ticker": "NVDA", "date": "2026-01-01"})
    assert clone.status_code == 202 and clone.json()["id"] != run["id"]
    page = client.get("/api/v1/runs?limit=1").json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    second = client.get("/api/v1/runs", params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert len(second["items"]) == 1 and second["items"][0]["id"] != page["items"][0]["id"]
    assert client.get("/api/v1/runs?limit=0").status_code == 422
    assert client.get("/api/v1/runs/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/runs/{run['id']}/report").status_code == 404


def test_sse_replays_durable_events_and_validates_run_scoped_cursor(research_api):
    client, _, _, _ = research_api
    run_id = start(client).json()["id"]
    client.post(f"/api/v1/runs/{run_id}/cancel")
    response = client.get(f"/api/v1/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert f"id: {run_id}:1" in response.text and f"id: {run_id}:2" in response.text
    assert '"attempt_id":null' in response.text and '"status":"cancelled"' in response.text
    replay = client.get(f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": f"{run_id}:1"})
    assert f"id: {run_id}:1" not in replay.text and f"id: {run_id}:2" in replay.text
    assert client.get(f"/api/v1/runs/{run_id}/events", params={"cursor": f"{run_id}:2"}).text == ""
    for cursor in (f"{uuid4()}:1", f"{run_id}:999", "invalid", f"{run_id}:-1"):
        assert (
            client.get(f"/api/v1/runs/{run_id}/events", params={"cursor": cursor}).status_code
            == 422
        )
    log = client.get(f"/api/v1/runs/{run_id}/event-log?after_seq=1").json()
    assert len(log["events"]) == 1 and log["events"][0]["seq"] == 2


def test_terminal_transition_between_sse_reads_does_not_drop_last_event(research_api, monkeypatch):
    client, service, workspace, _ = research_api
    run_id = start(client).json()["id"]
    original = service.list_events
    switched = [False]

    def transition(*args, **kwargs):
        rows = original(*args, **kwargs)
        if not switched[0] and kwargs.get("after_seq") == 0:
            switched[0] = True
            service.cancel_run(
                run_id, workspace_id=workspace.workspace_id, owner_id=workspace.user_id
            )
        return rows

    monkeypatch.setattr(service, "list_events", transition)
    response = client.get(f"/api/v1/runs/{run_id}/events")
    assert f"id: {run_id}:1" in response.text and f"id: {run_id}:2" in response.text


def test_queries_sse_exports_and_clone_check_scope(research_api):
    client, _, _, active_scope = research_api
    run_id = start(client).json()["id"]
    active_scope[0] = LocalWorkspace("foreign-user", "foreign-workspace", "", "Foreign")
    for path in ("", "/manifest", "/event-log", "/events", "/report", "/report.md"):
        assert client.get(f"/api/v1/runs/{run_id}{path}").status_code == 404
    for action in ("cancel", "resume", "clone"):
        assert client.post(f"/api/v1/runs/{run_id}/{action}").status_code == 404
