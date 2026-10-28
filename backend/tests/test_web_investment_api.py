"""The investment system is private, versioned, and frozen before research starts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app

BASE = "http://127.0.0.1:8000"
PASSWORD = "synthetic investment fixture password 123"


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def request(actor, method, path, **kwargs):
    return actor.client.request(method, path, headers=actor.headers, **kwargs)


@pytest.fixture
def investment_site(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.llm_clients.chatgpt_auth.authorization_status", lambda: {"status": "not_authorized"})
    monkeypatch.setattr("tradingagents.llm_clients.chatgpt_auth.signed_in_as", lambda: None)
    config = deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(tmp_path), llm_provider="openai", quick_think_provider=None, deep_think_provider=None)
    app = create_app(config, secret_store=MemorySecretStore(), instrument_search=lambda _: [],
        model_tester=lambda *_: {"chat": True, "tools": "supported"})
    token = app.state.authentication_service.ensure_setup_token().read_text().strip()

    def actor(client, path, body):
        status = checked(client.post(path, json=body, headers={"Origin": BASE}), 201)
        return SimpleNamespace(client=client, headers={"Origin": BASE, "X-CSRF-Token": status["csrf_token"],
            "X-Workspace-ID": status["user"]["workspace"]["id"]})

    with TestClient(app, base_url=BASE) as first, TestClient(app, base_url=BASE) as second:
        owner = actor(first, "/api/v1/auth/setup", {"username": "invest_owner", "password": PASSWORD, "setup_token": token})
        friend = actor(second, "/api/v1/auth/register", {"username": "invest_friend", "password": PASSWORD})
        yield app, owner, friend


def test_cookie_policy_plans_and_csrf_are_isolated(investment_site):
    app, owner, friend = investment_site
    initial = checked(request(owner, "GET", "/api/v1/investment-policy"))
    assert initial["revision"] == 0 and not initial["saved"]
    settings = {key: value for key, value in initial.items() if key not in {"saved", "updated_at", "scope_notice"}}
    settings["risk_limits"]["max_single_position_pct"] = 10
    settings["tax_notes"] = "private-tax-fixture"
    saved = checked(request(owner, "PUT", "/api/v1/investment-policy", json=settings))
    assert saved["revision"] == 1
    assert checked(request(friend, "GET", "/api/v1/investment-policy"))["risk_limits"]["max_single_position_pct"] is None
    assert request(owner, "PUT", "/api/v1/investment-policy", json=settings).status_code == 409
    assert owner.client.put("/api/v1/investment-policy", json=settings, headers={"Origin": BASE}).status_code == 403
    plan = checked(request(owner, "POST", "/api/v1/investment-plans", json={
        "ticker": "NVDA", "analysis_date": "2024-01-02", "title": "Private HBM thesis",
        "entry_price": 100, "fair_value": 125, "risk_inputs": {"capital_total_usd": 123456,
            "available_cash_usd": 34567, "target_position_pct": 15}}), 201)
    assert plan["policy_snapshot"]["revision"] == 1
    assert plan["evaluation"]["status"] == "breach" and not plan["evaluation"]["eligible_for_rating"]
    assert checked(request(friend, "GET", "/api/v1/investment-plans"))["items"] == []
    assert request(friend, "GET", f"/api/v1/investment-plans/{plan['id']}").status_code == 404
    assert request(friend, "PATCH", f"/api/v1/investment-plans/{plan['id']}", json={
        "ticker": "NVDA", "analysis_date": "2024-01-02", "revision": 1}).status_code == 404
    # The new routes stay behind the same session middleware as other private data.
    with TestClient(app, base_url=BASE) as anonymous:
        assert anonymous.get("/api/v1/investment-policy").status_code == 401
        assert anonymous.get("/api/v1/investment-plans").status_code == 401


def test_research_api_freezes_rules_and_excludes_private_portfolio_amounts(investment_site):
    _app, owner, _friend = investment_site
    plan = checked(request(owner, "POST", "/api/v1/investment-plans", json={
        "ticker": "NVDA", "analysis_date": "2024-01-02", "business": "HBM demand to verify",
        "risk_inputs": {"capital_total_usd": 123456789, "available_cash_usd": 3456789,
            "essential_cash_usd": 876543, "target_position_pct": 10}}), 201)
    run = checked(request(owner, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": "2024-01-02",
        "apply_investment_system": True, "investment_plan_id": plan["id"]}), 202)
    manifest = checked(request(owner, "GET", f"/api/v1/runs/{run['id']}/manifest"))
    packet = manifest["selection"]["investment_system"]
    assert packet["snapshot"]["policy"]["revision"] == 0
    assert packet["snapshot"]["plan"]["id"] == plan["id"]
    assert "HBM demand to verify" in packet["prompt"]
    public = str(packet)
    assert all(secret not in public for secret in ("123456789", "3456789", "876543", "capital_total_usd", "available_cash_usd"))
    current = checked(request(owner, "GET", "/api/v1/investment-policy"))
    update = {key: value for key, value in current.items() if key not in {"saved", "updated_at", "scope_notice"}}
    update["name"] = "Changed after enqueue"
    checked(request(owner, "PUT", "/api/v1/investment-policy", json=update))
    unchanged = checked(request(owner, "GET", f"/api/v1/runs/{run['id']}/manifest"))
    assert unchanged["selection"]["investment_system"] == packet
    assert request(owner, "POST", "/api/v1/runs", json={"ticker": "AAPL", "date": "2024-01-02",
        "apply_investment_system": True, "investment_plan_id": plan["id"]}).status_code == 422
    assert request(owner, "POST", "/api/v1/runs", json={"ticker": "NVDA", "date": "2024-01-02",
        "apply_investment_system": False, "investment_plan_id": plan["id"]}).status_code == 422
