"""Cookie-to-worker local Agent integration using only temporary offline fixtures."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tradingagents.dataflows.evidence import observed_call
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.api.schemas import EventEnvelope
from tradingagents.web.application.runtime import create_runtime_resolver
from tradingagents.web.application.runtime_connections import RuntimeConnectionService
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app
from tradingagents.web.workers.research import ResearchWorker

BASE = "http://127.0.0.1:8000"
DAY = "2024-01-02"
PASSWORD = "temporary offline fixture password 123"
PRIVATE_LOG = "private-runtime-log-must-never-be-published"
PRICES = (
    "Date,Open,High,Low,Close,Volume\n"
    "2023-12-29,100,110,95,105,1000\n"
    "2024-01-02,105,115,100,110,1200\n"
)


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def actor(client, status):
    return SimpleNamespace(client=client, status=status, user_id=status["user"]["id"],
        workspace_id=status["user"]["workspace"]["id"], headers={"Origin": BASE,
            "X-CSRF-Token": status["csrf_token"], "X-Workspace-ID": status["user"]["workspace"]["id"]})


def request(user, method, path, **kwargs):
    headers = dict(user.headers)
    headers.update(kwargs.pop("headers", {}))
    return user.client.request(method, path, headers=headers, **kwargs)


def ready_probe(_options):
    return {"program_version": "0.164.0", "auth_status": "authorized", "auth_fingerprint": "a" * 64,
        "capabilities": {"dynamic_tools": True, "restricted_tools": True, "cancellation": True},
        "models": [{"id": "test-model"}], "raw_log": PRIVATE_LOG}


@pytest.fixture
def site(tmp_path, monkeypatch):
    # These are synthetic credentials. This fixture never reads real CLI auth or launches a program.
    monkeypatch.setattr("tradingagents.llm_clients.chatgpt_auth.authorization_status",
        lambda: {"status": "not_authorized"})
    monkeypatch.setattr("tradingagents.llm_clients.chatgpt_auth.signed_in_as", lambda: None)
    config = deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(tmp_path / "results"), llm_provider="openai",
        quick_think_provider=None, deep_think_provider=None)
    secrets = MemorySecretStore()
    app = create_app(config, secret_store=secrets, runtime_probe=ready_probe,
        model_tester=lambda *_: {"chat": True, "tools": "supported"}, instrument_search=lambda _: [],
        research_data_tester=lambda *_: {"success": True})
    database = app.state.database
    executable = tmp_path / "offline-codex"
    executable.write_text("#!/usr/bin/env python3\nraise RuntimeError('fixture must never execute')\n")
    executable.chmod(0o700)
    token = app.state.authentication_service.ensure_setup_token().read_text().strip()
    control = SimpleNamespace(behavior="normal", started=0, invocations=[], references=[],
        tool_errors=[], run_id=None, adapter_options=[])

    with TestClient(app, base_url=BASE) as owner_client:
        owner = actor(owner_client, checked(owner_client.post("/api/v1/auth/setup", json={
            "username": "runtime_owner", "password": PASSWORD, "setup_token": token},
            headers={"Origin": BASE}), 201))
        with TestClient(app, base_url=BASE) as friend_client:
            friend = actor(friend_client, checked(friend_client.post("/api/v1/auth/register", json={
                "username": "runtime_friend", "password": PASSWORD}, headers={"Origin": BASE}), 201))
            connection = checked(request(owner, "POST", "/api/v1/runtime-connections", json={
                "name": "Offline owner runtime", "model_id": "test-model", "enabled": True,
                "executable_path": str(executable)}), 201)
            tested = checked(request(owner, "POST", f"/api/v1/runtime-connections/{connection['id']}/test",
                json={"revision": connection["revision"]}))
            assert tested["success"] and tested["connection"]["readiness"] == "ready"
            connection = tested["connection"]

            def invoker(method, *args):
                control.invocations.append((method, args))
                assert method == "get_stock_data" and args[0] == "NVDA" and args[-1] == DAY
                return observed_call(method, "yfinance", args, {}, lambda: PRICES)

            class OfflineAdapter:
                def __init__(self, path, **options):
                    assert path == str(executable)
                    control.adapter_options.append(options)

                def run(self, **options):
                    control.started += 1
                    options["active"]()
                    options["emit"]("runtime_session", {"raw_log": PRIVATE_LOG})
                    definitions = {item["name"]: item for item in options["tools"]}
                    assert set(definitions) == {"research_prices", "research_indicators", "research_company",
                        "research_news", "research_macro", "research_context"}
                    response = json.loads(options["on_tool"]("research_prices", {"look_back_days": 20}))
                    reference = response["evidence"][0]["reference"]
                    control.references.append(reference)
                    if control.behavior == "cancel":
                        app.state.research_service.cancel_run(control.run_id,
                            workspace_id=owner.workspace_id, owner_id=owner.user_id)
                        options["active"]()
                    if control.behavior == "tool_budget":
                        options["on_tool"]("research_prices", {"look_back_days": 10})
                    if control.behavior == "forbidden_tool":
                        options["on_tool"]("shell", {"command": "cat private-file"})
                    if control.behavior == "bad_arguments":
                        for extra in ({"symbol": "AAPL"}, {"date": "2026-01-01"},
                            {"shell": "cat private-file"}, {"url": "https://unapproved.example/"}):
                            rejected = json.loads(options["on_tool"]("research_prices", extra))
                            control.tool_errors.append(rejected)
                    options["emit"]("runtime_message", {"message": PRIVATE_LOG, "text": PRIVATE_LOG})
                    report = {"ticker": "AAPL" if control.behavior == "wrong_ticker" else "NVDA",
                        "analysis_date": DAY, "summary": '<script>alert("runtime-xss")</script> Offline summary',
                        "analysis": "Analysis based on the captured historical price fixture.",
                        "bull_case": "Conditional upside scenario.", "bear_case": "Conditional downside scenario.",
                        "risks": "Historical coverage is limited; reassess if conditions change.",
                        "conclusion": "Observe; this is an unverified research opinion.",
                        "evidence_refs": [f"snapshot:{uuid4()}" if control.behavior == "forged_reference" else reference],
                        "source_coverage": "complete"}
                    return {"report": "not JSON" if control.behavior == "invalid_report" else json.dumps(report),
                        "effective_model": "wrong-model" if control.behavior == "wrong_model" else "test-model",
                        "effective_provider": "offline-fixture", "auth_source": "runtime_login",
                        "usage": {"input_tokens": 11, "output_tokens": 23}, "raw_log": PRIVATE_LOG}

            worker = ResearchWorker(app.state.research_service,
                create_runtime_resolver(database, config, secrets),
                runtime_service_factory=lambda scope: RuntimeConnectionService(database, scope,
                    secret_store=secrets, probe=ready_probe), runtime_adapter_factory=OfflineAdapter,
                runtime_tool_invoker=invoker)
            yield SimpleNamespace(app=app, database=database, owner=owner, friend=friend,
                connection=connection, executable=executable, control=control, worker=worker)


def enqueue(site, **overrides):
    body = {"ticker": "NVDA", "date": DAY, "objective": "technical", "execution_mode": "local_agent",
        "runtime_connection_id": site.connection["id"], "max_runtime_seconds": 60, **overrides}
    result = checked(request(site.owner, "POST", "/api/v1/runs", json=body), 202)
    site.control.run_id = result["id"]
    return result


def snapshot(site, run_id):
    return checked(request(site.owner, "GET", f"/api/v1/runs/{run_id}"))


def evidence(site, run_id):
    return checked(request(site.owner, "GET", f"/api/v1/runs/{run_id}/evidence?include_body=true"))


def test_cookie_runtime_worker_tools_evidence_and_safe_exports(site):
    queued = enqueue(site)
    assert queued["execution_mode"] == "local_agent" and not queued["checkpoint"]
    assert queued["status"] == "queued"
    assert site.worker.run_once() and not site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "completed", result
    assert site.control.started == 1 and len(site.control.invocations) == 1
    assert result["rating"] is None and result["raw_rating"] is None
    assert result["quality"]["eligible_for_rating"] is False
    assert "组合经理" not in json.dumps(result, ensure_ascii=False)
    assert result["quality"]["required_categories"] == ["market"]
    assert result["quality"]["rule_version"] == "local-agent-evidence-v1"
    page = evidence(site, queued["id"])
    assert page["capture_started"] and len(page["items"]) == 1
    item = page["items"][0]
    assert site.control.references == [f"snapshot:{item['snapshot_id']}"]
    assert item["provider"] == "yfinance" and item["method"] == "get_stock_data"
    assert item["requested_as_of"] == DAY and item["body"]
    assert item["snapshot_id"] in result["reports"]["runtime_evidence"]
    metadata = result["runtime_execution"]
    assert metadata["requested_model"] == metadata["effective_model"] == "test-model"
    assert metadata["effective_provider"] == "offline-fixture" and metadata["auth_source"] == "runtime_login"
    assert metadata["llm_calls_known"] is False and metadata["resume_supported"] is False
    assert result["stats"]["tool_calls"] == 1
    assert result["stats"]["tokens_in"] == 11 and result["stats"]["tokens_out"] == 23
    assert result["stats"]["usage_quality"] == "partial"
    events = checked(request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/event-log"))["events"]
    for event in events:
        EventEnvelope.model_validate(event)
    runtime_events = [event["payload"]["event"] for event in events if event["type"] == "runtime"]
    assert {"preparing", "session_started", "tool_started", "tool_completed", "completed"} <= set(runtime_events)
    html = request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/report")
    markdown = request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/report.md")
    assert html.status_code == markdown.status_code == 200
    assert "<script>" not in html.text and "&lt;script&gt;" in html.text
    assert "runtime-xss" in markdown.text
    manifest = checked(request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/manifest"))
    public = json.dumps([result, page, events, manifest]) + html.text + markdown.text
    assert PRIVATE_LOG not in public and "a" * 64 not in public
    assert str(site.executable) not in json.dumps(manifest)


def test_runtime_disabled_after_enqueue_never_starts_adapter(site):
    queued = enqueue(site)
    changed = checked(request(site.owner, "PATCH", f"/api/v1/runtime-connections/{site.connection['id']}",
        json={"revision": site.connection["revision"], "enabled": False}))
    assert not changed["enabled"]
    assert site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "failed" and site.control.started == 0
    assert not site.control.invocations and not result["report_url"]


def test_runtime_cancellation_stops_next_activity_and_retains_evidence(site):
    queued = enqueue(site)
    site.control.behavior = "cancel"
    assert site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "cancelled" and site.control.started == 1
    assert len(evidence(site, queued["id"])["items"]) == 1
    assert not result["report_url"] and not result["runtime_execution"]


def test_gateway_tool_budget_blocks_second_data_call(site):
    queued = enqueue(site, max_tool_calls=1)
    site.control.behavior = "tool_budget"
    assert site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "failed" and len(site.control.invocations) == 1
    assert result["stats"]["tool_calls"] == 1
    events = checked(request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/event-log"))["events"]
    assert any(event["type"] == "error" and event["payload"]["error_code"] == "runtime_tool_budget_exceeded"
        for event in events)
    assert len(evidence(site, queued["id"])["items"]) == 1


def test_other_cookie_cannot_use_host_runtime_or_read_its_research(site):
    own = enqueue(site)
    assert checked(request(site.friend, "GET", "/api/v1/runtime-connections"))["connections"] == []
    denied = request(site.friend, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": DAY,
        "execution_mode": "local_agent", "runtime_connection_id": site.connection["id"]})
    assert denied.status_code == 403 and denied.json()["error_code"] == "runtime_host_forbidden"
    for suffix in ("", "/manifest", "/event-log", "/evidence", "/report", "/report.md"):
        assert request(site.friend, "GET", f"/api/v1/runs/{own['id']}{suffix}").status_code == 404
    forged = request(site.friend, "GET", "/api/v1/runtime-connections",
        headers={"X-Workspace-ID": site.owner.workspace_id})
    assert forged.status_code == 401 and site.control.started == 0


@pytest.mark.parametrize("behavior,error_code", [
    ("forged_reference", "runtime_evidence_reference_invalid"),
    ("wrong_ticker", "runtime_report_scope_mismatch"),
    ("wrong_model", "runtime_model_mismatch"),
    ("invalid_report", "runtime_report_invalid"),
    ("forbidden_tool", "runtime_tool_forbidden"),
])
def test_bad_runtime_result_fails_without_losing_captured_sources(site, behavior, error_code):
    queued = enqueue(site)
    site.control.behavior = behavior
    assert site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "failed" and not result["report_url"]
    assert len(evidence(site, queued["id"])["items"]) == 1
    events = checked(request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/event-log"))["events"]
    assert any(event["type"] == "error" and event["payload"]["error_code"] == error_code for event in events)
    assert PRIVATE_LOG not in json.dumps([result, events])


def test_repeated_http_submission_runs_once_and_runtime_resume_is_explicitly_rejected(site):
    body = {"ticker": "NVDA", "date": DAY, "objective": "technical", "execution_mode": "local_agent",
        "runtime_connection_id": site.connection["id"]}
    headers = {"Idempotency-Key": "offline-runtime-idempotency"}
    first = checked(request(site.owner, "POST", "/api/v1/runs", json=body, headers=headers), 202)
    second = checked(request(site.owner, "POST", "/api/v1/runs", json=body, headers=headers), 202)
    assert first["id"] == second["id"]
    assert site.worker.run_once() and not site.worker.run_once() and site.control.started == 1
    retry = checked(request(site.owner, "POST", "/api/v1/runs", json=body, headers=headers), 202)
    assert retry["id"] == first["id"] and retry["status"] == "completed"
    assert not site.worker.run_once() and site.control.started == 1
    conflict = request(site.owner, "POST", "/api/v1/runs", json={**body, "research_instructions": "changed"},
        headers=headers)
    assert conflict.status_code == 409 and conflict.json()["error_code"] == "idempotency_conflict"
    # Failed Runtime runs have no supported session/checkpoint recovery path.
    failed = enqueue(site)
    site.control.behavior = "wrong_ticker"
    assert site.worker.run_once()
    resumed = request(site.owner, "POST", f"/api/v1/runs/{failed['id']}/resume")
    assert resumed.status_code == 409 and resumed.json()["error_code"] == "runtime_resume_unsupported"


@pytest.mark.parametrize("body", [
    {"execution_mode": "builtin_workflow", "runtime_connection_id": "selected"},
    {"execution_mode": "local_agent"},
    {"execution_mode": "local_agent", "runtime_connection_id": "selected", "template_version_id": "template"},
])
def test_http_rejects_conflicting_or_missing_execution_selection(site, body):
    payload = {"ticker": "NVDA", "date": DAY, **body}
    if payload.get("runtime_connection_id") == "selected":
        payload["runtime_connection_id"] = site.connection["id"]
    assert request(site.owner, "POST", "/api/v1/runs", json=payload).status_code == 422
    assert not site.control.invocations and site.control.started == 0


def test_gateway_cannot_override_stock_date_shell_or_url(site):
    queued = enqueue(site)
    site.control.behavior = "bad_arguments"
    assert site.worker.run_once()
    assert snapshot(site, queued["id"])["status"] == "completed"
    assert len(site.control.invocations) == 1 and len(site.control.tool_errors) == 4
    assert all(result["error"] == "invalid_tool_input" for result in site.control.tool_errors)
    assert not any("AAPL" in json.dumps(result) or "unapproved.example" in json.dumps(result)
        for result in site.control.tool_errors)


def test_reported_token_budget_fails_after_recording_usage_and_evidence(site):
    queued = enqueue(site, max_tokens_total=20)
    assert site.worker.run_once()
    result = snapshot(site, queued["id"])
    assert result["status"] == "failed" and not result["report_url"]
    assert result["stats"]["tokens_in"] == 11 and result["stats"]["tokens_out"] == 23
    assert len(evidence(site, queued["id"])["items"]) == 1
    events = checked(request(site.owner, "GET", f"/api/v1/runs/{queued['id']}/event-log"))["events"]
    assert any(event["type"] == "error" and event["payload"]["error_code"] == "research_budget_exceeded"
        for event in events)
