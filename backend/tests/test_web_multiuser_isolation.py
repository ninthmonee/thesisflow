"""Cookie-to-business-scope integration; temporary accounts and offline artifacts only."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.application.authentication import SESSION_COOKIE, AuthenticationService
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.research_quality import normalize_observation
from tradingagents.web.infrastructure.models import Report
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app

BASE = "http://127.0.0.1:8000"
PASSWORD = "temporary test password 123"
DAY = "2024-01-02"


def search(_query):
    return [{"symbol": "NVDA", "name": "NVIDIA", "exchange": "NMS", "asset_type": "stock",
        "currency": "USD", "timezone": "America/New_York"}]


def actor(client, status):
    return SimpleNamespace(client=client, status=status, user_id=status["user"]["id"],
        workspace_id=status["user"]["workspace"]["id"], headers={"Origin": BASE,
            "X-CSRF-Token": status["csrf_token"], "X-Workspace-ID": status["user"]["workspace"]["id"]})


def request(user, method, path, **kwargs):
    headers = dict(user.headers)
    headers.update(kwargs.pop("headers", {}))
    return user.client.request(method, path, headers=headers, **kwargs)


def response_json(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture
def site(tmp_path, monkeypatch):
    # All credentials here are fake. Do not inspect the developer's OAuth file.
    monkeypatch.setenv("OPENAI_API_KEY", "host-test-only-key")
    monkeypatch.setenv("FRED_API_KEY", "host-test-only-fred")
    monkeypatch.setattr("tradingagents.llm_clients.chatgpt_auth.authorization_status",
        lambda: {"status": "authorized", "identity_fingerprint": "a" * 64})
    config = deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(tmp_path / "results"), llm_provider="openai",
        quick_think_provider=None, deep_think_provider=None)
    app = create_app(config, secret_store=MemorySecretStore(), instrument_search=search,
        model_tester=lambda *_: {"chat": True, "tools": "supported"},
        research_data_tester=lambda *_: {"success": True}, durable_research=True,
        authentication_required=True)
    database = app.state.database
    local = database.local_workspace()
    original_group = app.state.watchlist_service.list_watchlists()[0]
    original_group = app.state.watchlist_service.rename_watchlist(
        original_group["id"], "原本机自选", original_group["revision"])
    legacy_run = app.state.research_service.create_run({"ticker": "NVDA", "date": DAY},
        workspace_id=local.workspace_id, owner_id=local.user_id)
    app.state.research_service.tasks.finish(app.state.research_service.tasks.claim_next("fixture"), "completed")
    token = AuthenticationService(database).ensure_setup_token().read_text().strip()
    with TestClient(app, base_url=BASE) as owner_client:
        owner = actor(owner_client, response_json(owner_client.post("/api/v1/auth/setup",
            json={"username": "owner", "password": PASSWORD, "setup_token": token},
            headers={"Origin": BASE}), 201))
        friend_client = TestClient(app, base_url=BASE)
        friend = actor(friend_client, response_json(friend_client.post("/api/v1/auth/register",
            json={"username": "friend", "password": PASSWORD}, headers={"Origin": BASE}), 201))
        anonymous = TestClient(app, base_url=BASE)
        try:
            yield SimpleNamespace(app=app, database=database, owner=owner, friend=friend,
                anonymous=anonymous, local=local, legacy_run=legacy_run, original_group=original_group)
        finally:
            anonymous.close()
            friend_client.close()


def configure_model(user):
    connection = response_json(request(user, "POST", "/api/v1/model-connections", json={
        "name": f"{user.user_id} model", "provider": "deepseek", "api_key": f"fake-{user.user_id}"}), 201)
    profile = response_json(request(user, "POST", "/api/v1/model-profiles", json={
        "connection_id": connection["id"], "model_id": "deepseek-chat"}), 201)
    tested = response_json(request(user, "POST", f"/api/v1/model-profiles/{profile['id']}/test",
        json={"revision": profile["revision"]}))
    assert tested["success"]
    template = response_json(request(user, "PUT", "/api/v1/model-settings", json={
        "default_profile_id": profile["id"], "expected_version": 0}))
    return connection, tested["profile"], template


def completed_with_artifacts(site, user):
    # Seed an already finished offline job; this fixture never invokes a graph/model.
    service = ResearchService(site.database, site.app.state.research_service.artifacts_root)
    run = service.create_run({"ticker": "NVDA", "date": DAY, "analysts": ["market"]},
        workspace_id=user.workspace_id, owner_id=user.user_id)
    claim = service.tasks.claim_next("offline-fixture")
    assert claim.run_id == run["id"]
    evidence = site.app.state.research_evidence_service
    evidence.begin(claim, service.tasks.manifest(claim)["content"])
    details, body = normalize_observation("get_stock_data", "yfinance", ("NVDA", "2023-12-01", DAY), {},
        "Date,Open,High,Low,Close,Volume\n2024-01-02,100,110,95,105,1000\n", "success", None,
        symbol="NVDA", analysis_date=DAY, received_at=datetime.now(UTC))
    evidence.record(claim, details, body)
    service.tasks.publish(claim, reports={"market_report": f"Private research for {user.user_id}"})
    service.tasks.finish(claim, "completed")
    folder = service.run_directory(user.workspace_id, run["id"])
    folder.mkdir(parents=True, exist_ok=True)
    with site.database.session() as session:
        for suffix, kind in (("html", "_export_html"), ("md", "_export_markdown")):
            path = folder / f"report.{suffix}"
            path.write_text(f"Private report for {user.user_id}")
            session.add(Report(workspace_id=user.workspace_id, run_id=run["id"], kind=kind,
                file_path=str(path.relative_to(service.artifacts_root)),
                content_hash=hashlib.sha256(path.read_bytes()).hexdigest()))
    return run


def test_anonymous_cannot_read_business_or_legacy_routes(site):
    run = site.legacy_run["id"]
    for path in ("/api/v1/bootstrap", "/api/v1/watchlists", "/api/v1/themes", "/api/v1/model-providers",
        "/api/v1/model-connections", "/api/v1/model-profiles", "/api/v1/model-settings", "/api/v1/data-connections",
        "/api/v1/research-data-connections", "/api/v1/data-status", "/api/v1/data-settings", "/api/v1/runs",
        f"/api/v1/runs/{run}", f"/api/v1/runs/{run}/manifest", f"/api/v1/runs/{run}/evidence",
        f"/api/v1/runs/{run}/report.md", "/api/v1/viewpoints", "/api/config", "/api/runs",
        f"/api/runs/{run}", f"/api/runs/{run}/report.md"):
        response = site.anonymous.get(path, headers={"X-Workspace-ID": site.owner.workspace_id})
        assert response.status_code == 401, (path, response.text)
    assert site.anonymous.post("/api/v1/watchlists", json={"name": "intruder"},
        headers={"Origin": BASE, "X-Workspace-ID": site.owner.workspace_id}).status_code == 401
    assert site.anonymous.post("/api/runs", json={"ticker": "NVDA", "date": DAY},
        headers={"Origin": BASE}).status_code == 401


def test_owner_claim_preserves_legacy_data_and_registration_starts_empty(site):
    assert site.owner.user_id == site.local.user_id
    assert site.owner.workspace_id == site.local.workspace_id
    assert str(UUID(site.friend.user_id)) == site.friend.user_id
    assert str(UUID(site.friend.workspace_id)) == site.friend.workspace_id
    assert site.owner.workspace_id != site.friend.workspace_id and site.owner.user_id != site.friend.user_id
    owner_groups = response_json(request(site.owner, "GET", "/api/v1/watchlists"))
    assert owner_groups[0]["id"] == site.original_group["id"] and owner_groups[0]["name"] == "原本机自选"
    assert [run["id"] for run in response_json(request(site.owner, "GET", "/api/v1/runs"))["items"]] == [site.legacy_run["id"]]
    assert response_json(request(site.friend, "GET", "/api/v1/runs"))["items"] == []
    friend_groups = response_json(request(site.friend, "GET", "/api/v1/watchlists"))
    assert len(friend_groups) == 1 and friend_groups[0]["items"] == []
    assert friend_groups[0]["id"] != owner_groups[0]["id"]
    for path in ("model-connections", "model-profiles", "data-connections", "research-data-connections"):
        result = response_json(request(site.friend, "GET", "/api/v1/" + path))
        assert list(result.values()) == [[]]
    assert response_json(request(site.friend, "GET", "/api/v1/model-settings"))["default_template"] is None


def test_csrf_and_expected_workspace_do_not_grant_authorization(site):
    owner, friend = site.owner, site.friend
    assert request(friend, "GET", "/api/v1/watchlists", headers={"X-Workspace-ID": owner.workspace_id}).status_code == 401
    assert request(friend, "POST", "/api/v1/watchlists", json={"name": "bad"},
        headers={"X-CSRF-Token": owner.status["csrf_token"]}).status_code == 403
    assert friend.client.post("/api/v1/watchlists", json={"name": "bad"}, headers={"Origin": BASE,
        "X-Workspace-ID": friend.workspace_id}).status_code == 403
    # A delayed request from the previous account must not mutate the newly signed-in account.
    switched = TestClient(site.app, base_url=BASE)
    try:
        switched.cookies.set(SESSION_COOKIE, friend.client.cookies.get(SESSION_COOKIE))
        response = switched.post("/api/v1/watchlists", json={"name": "stale browser tab"}, headers=owner.headers)
        assert response.status_code == 401
    finally:
        switched.close()
    assert len(response_json(request(friend, "GET", "/api/v1/watchlists"))) == 1


def test_themes_watchlists_model_profiles_and_data_accounts_are_private(site):
    owner, friend = site.owner, site.friend
    theme = response_json(request(owner, "POST", "/api/v1/themes", json={
        "name": "Owner private basket", "members": [{"symbol": "NVDA"}]}), 201)
    group = response_json(request(owner, "POST", "/api/v1/watchlists", json={"name": "Owner private list"}), 201)
    connection, profile, _ = configure_model(owner)
    research_data = response_json(request(owner, "POST", "/api/v1/research-data-connections", json={
        "provider": "fred", "name": "Owner FRED", "api_key": "test-only-owner-fred"}), 201)
    alternative = response_json(request(owner, "POST", "/api/v1/data-connections", json={
        "provider": "massive", "name": "Owner news", "api_key": "test-only-owner-massive"}), 201)
    assert all(row["id"] != theme["id"] for row in response_json(request(friend, "GET", "/api/v1/themes"))["items"])
    assert all(row["id"] != group["id"] for row in response_json(request(friend, "GET", "/api/v1/watchlists")))
    for path in (f"/api/v1/themes/{theme['id']}", f"/api/v1/themes/{theme['id']}/versions",
        f"/api/v1/model-connections/{connection['id']}/models"):
        assert request(friend, "GET", path).status_code == 404
    writes = [
        ("PATCH", f"/api/v1/themes/{theme['id']}/pin", {"revision": 1, "pinned": True}),
        ("PATCH", f"/api/v1/watchlists/{group['id']}", {"revision": 1, "name": "hijacked"}),
        ("PATCH", f"/api/v1/model-connections/{connection['id']}", {"revision": connection["revision"], "name": "hijacked"}),
        ("PATCH", f"/api/v1/model-profiles/{profile['id']}", {"revision": profile["revision"], "model_id": "hijacked"}),
        ("POST", f"/api/v1/model-profiles/{profile['id']}/test", {"revision": profile["revision"]}),
        ("PATCH", f"/api/v1/research-data-connections/{research_data['id']}", {"revision": 1, "name": "hijacked"}),
        ("POST", f"/api/v1/research-data-connections/{research_data['id']}/clear", {"revision": 1}),
        ("PATCH", f"/api/v1/data-connections/{alternative['id']}", {"revision": 1, "name": "hijacked"}),
    ]
    for method, path, body in writes:
        assert request(friend, method, path, json=body).status_code == 404, path
    assert request(friend, "POST", "/api/v1/model-profiles", json={
        "connection_id": connection["id"], "model_id": "deepseek-chat"}).status_code == 404
    assert request(friend, "PUT", "/api/v1/model-settings", json={
        "default_profile_id": profile["id"]}).status_code == 404
    assert response_json(request(owner, "GET", f"/api/v1/themes/{theme['id']}"))["name"] == theme["name"]
    for path in ("model-connections", "model-profiles", "data-connections", "research-data-connections"):
        assert list(response_json(request(friend, "GET", "/api/v1/" + path)).values()) == [[]]


def test_run_manifests_reports_evidence_context_and_viewpoints_check_cookie_owner(site):
    owner_run = completed_with_artifacts(site, site.owner)
    friend_run = completed_with_artifacts(site, site.friend)
    for current, foreign, current_run, foreign_run in ((site.owner, site.friend, owner_run, friend_run),
        (site.friend, site.owner, friend_run, owner_run)):
        identifier = current_run["id"]
        assert response_json(request(current, "GET", f"/api/v1/runs/{identifier}"))["id"] == identifier
        manifest = response_json(request(current, "GET", f"/api/v1/runs/{identifier}/manifest"))
        assert (manifest["workspace_id"], manifest["owner_id"]) == (current.workspace_id, current.user_id)
        page = response_json(request(current, "GET", f"/api/v1/runs/{identifier}/evidence?include_body=true"))
        assert page["capture_started"] and len(page["items"]) == 1 and page["items"][0]["body"]
        assert request(current, "GET", f"/api/v1/runs/{identifier}/report.md").text == f"Private report for {current.user_id}"
        assert request(current, "GET", f"/api/runs/{identifier}/report.md").status_code == 200
        for suffix in ("", "/manifest", "/event-log", "/events", "/report", "/report.md", "/evidence?include_body=true", "/quality"):
            denied = request(foreign, "GET", f"/api/v1/runs/{identifier}{suffix}")
            assert denied.status_code == 404, (suffix, denied.text)
            assert current.user_id not in denied.text
        for suffix in ("", "/report", "/report.md", "/events"):
            assert request(foreign, "GET", f"/api/runs/{identifier}{suffix}").status_code == 404
        for action in ("cancel", "resume", "clone"):
            assert request(foreign, "POST", f"/api/v1/runs/{identifier}/{action}",
                json={"ticker": "NVDA", "date": DAY} if action == "clone" else None).status_code == 404
        assert request(current, "GET", f"/api/v1/runs/{identifier}/compare",
            params={"other_run_id": foreign_run["id"]}).status_code == 404
        theme = response_json(request(current, "POST", "/api/v1/themes", json={"name": f"{current.user_id} context"}), 201)
        context = response_json(request(current, "POST", "/api/v1/research-context", json={
            "kind": "theme", "theme_id": theme["id"], "theme_version": 1}), 201)
        assert response_json(request(current, "GET", f"/api/v1/research-context/{context['id']}"))["id"] == context["id"]
        assert request(foreign, "GET", f"/api/v1/research-context/{context['id']}").status_code == 404
        assert request(foreign, "POST", "/api/v1/research-context", json={"kind": "theme", "theme_id": theme["id"]}).status_code == 404
        view = response_json(request(current, "POST", "/api/v1/viewpoints", json={
            "run_id": identifier, "confirmed": True, "thesis": "Private thesis", "invalidation": "Private condition"}), 201)
        assert response_json(request(foreign, "GET", "/api/v1/viewpoints", params={"run_id": identifier}))["items"] == []
        assert request(foreign, "PATCH", f"/api/v1/viewpoints/{view['id']}", json={
            "revision": 1, "thesis": "hijacked", "invalidation": "hijacked"}).status_code == 404
        assert request(foreign, "POST", "/api/v1/viewpoints", json={
            "run_id": identifier, "confirmed": True, "thesis": "hijacked", "invalidation": "hijacked"}).status_code == 404


def test_new_account_cannot_inherit_host_oauth_environment_or_template(site):
    friend = site.friend
    providers = response_json(request(friend, "GET", "/api/v1/model-providers"))["providers"]
    assert all(provider["id"] != "chatgpt" for provider in providers)
    denied = request(friend, "POST", "/api/v1/model-connections", json={
        "provider": "chatgpt", "auth_type": "oauth_local", "name": "host subscription"})
    assert denied.status_code == 403 and denied.json()["error_code"] == "local_model_access_forbidden"
    denied = request(friend, "POST", "/api/v1/research-data-connections", json={
        "provider": "fred", "credential_source": "legacy_environment", "name": "host account"})
    assert denied.status_code == 403 and denied.json()["error_code"] == "research_environment_forbidden"
    for path in ("/api/v1/runs", "/api/runs"):
        assert request(friend, "POST", path, json={"ticker": "NVDA", "date": DAY}).status_code == 409
    _, _, template = configure_model(site.owner)
    denied = request(friend, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": DAY,
        "template_version_id": template["id"]})
    assert denied.status_code in {404, 409} and site.owner.user_id not in denied.text
    bootstrap = response_json(request(friend, "GET", "/api/v1/bootstrap"))
    assert bootstrap["model"]["configured"] is False
    assert not any(source["configured"] for source in bootstrap["services"] if source["id"] == "fred")
    assert "host-test-only" not in str(bootstrap)
    legacy_config = response_json(request(friend, "GET", "/api/config"))
    assert legacy_config["model_ready"] is False
    assert not any(source["configured"] for source in legacy_config["services"] if source["id"] == "fred")
    status = response_json(request(friend, "GET", "/api/v1/data-status"))
    assert "host-test-only" not in str(status)
    assert all(not source["environment_configured"] for source in status["legacy_sources"])


def test_http_submission_freezes_only_current_accounts_models_and_data(site):
    runs = []
    for user in (site.owner, site.friend):
        connection, profile, _ = configure_model(user)
        source = response_json(request(user, "POST", "/api/v1/research-data-connections", json={
            "provider": "fred", "name": "Personal FRED", "api_key": f"data-only-{user.user_id}"}), 201)
        run = response_json(request(user, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": DAY},
            headers={"Idempotency-Key": "same-browser-request"}), 202)
        repeated = response_json(request(user, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": DAY},
            headers={"Idempotency-Key": "same-browser-request"}), 202)
        assert run["id"] == repeated["id"]
        frozen = response_json(request(user, "GET", f"/api/v1/runs/{run['id']}/manifest"))
        assert frozen["data_connections"]["providers"]["fred"]["connection_id"] == source["id"]
        models = frozen["selection"]["model_snapshot"]["roles"].values()
        assert all(model["profile_id"] == profile["id"] and model["connection_id"] == connection["id"]
            and model["workspace_id"] == user.workspace_id for model in models)
        assert f"fake-{user.user_id}" not in str(frozen) and f"data-only-{user.user_id}" not in str(frozen)
        runs.append(run["id"])
    assert runs[0] != runs[1]
    owner_ids = {row["id"] for row in response_json(request(site.owner, "GET", "/api/v1/runs"))["items"]}
    friend_ids = {row["id"] for row in response_json(request(site.friend, "GET", "/api/v1/runs"))["items"]}
    assert runs[0] in owner_ids and runs[1] not in owner_ids
    assert runs[1] in friend_ids and runs[0] not in friend_ids


def test_concurrent_requests_keep_current_cookie_scope_private(site):
    def read(index):
        user = site.owner if index % 2 else site.friend
        bootstrap = response_json(request(user, "GET", "/api/v1/bootstrap"))
        groups = response_json(request(user, "GET", "/api/v1/watchlists"))
        assert bootstrap["workspace"]["id"] == user.workspace_id
        return user.user_id, [row["id"] for row in groups]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(read, range(18)))
    owner_ids = {tuple(ids) for owner, ids in results if owner == site.owner.user_id}
    friend_ids = {tuple(ids) for owner, ids in results if owner == site.friend.user_id}
    assert len(owner_ids) == len(friend_ids) == 1 and owner_ids.isdisjoint(friend_ids)


def test_logout_revokes_even_copied_cookie_and_other_account_remains_active(site):
    cookie = site.friend.client.cookies.get(SESSION_COOKIE)
    copied = TestClient(site.app, base_url=BASE)
    try:
        copied.cookies.set(SESSION_COOKIE, cookie)
        assert copied.get("/api/v1/watchlists", headers=site.friend.headers).status_code == 200
        result = response_json(request(site.friend, "POST", "/api/v1/auth/logout"))
        assert result["authenticated"] is False
        assert copied.get("/api/v1/watchlists", headers=site.friend.headers).status_code == 401
        assert request(site.friend, "GET", "/api/v1/watchlists").status_code == 401
        assert request(site.owner, "GET", "/api/v1/watchlists").status_code == 200
    finally:
        copied.close()
