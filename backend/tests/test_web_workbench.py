"""The new UI and v1 metadata integrate without replacing the original research entry."""

from copy import deepcopy
from datetime import UTC, datetime

import pandas as pd
from fastapi.testclient import TestClient

from tests.test_web_market_data import ET, Provider, frame
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.infrastructure.seeds import LOCAL_USER_ID, LOCAL_WORKSPACE_ID
from tradingagents.web.server import create_app


def make_app(tmp_path, monkeypatch, *, ui=None):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-WORKBENCH-KEY")
    config = deepcopy(DEFAULT_CONFIG)
    config.update(
        results_dir=str(tmp_path / "state"),
        llm_provider="openai",
        quick_think_provider=None,
        deep_think_provider=None,
        backend_url="https://private:password@invalid.example/v1",
    )
    return create_app(config, workbench_dir=ui or tmp_path / "missing-ui", authentication_required=False)


def test_bootstrap_uses_persistent_local_identity_and_allowlisted_config(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/bootstrap")
        assert response.status_code == 200
        body = response.json()
        assert body["workspace"]["id"] == LOCAL_WORKSPACE_ID
        assert body["workspace"]["owner_id"] == LOCAL_USER_ID
        assert body["model"]["configured"] is True
        assert "PRIVATE-WORKBENCH-KEY" not in response.text
        assert "password" not in response.text
        assert "/Users/" not in response.text
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/config").status_code == 200
        assert client.get("/").status_code == 200
        assert app.state.database.schema_revision() == "0012_agent_runtimes"


def test_unbuilt_workbench_is_explicitly_unavailable(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/workbench/").status_code == 503


def test_spa_routes_and_assets_have_safe_fallbacks(tmp_path, monkeypatch):
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>Workbench</html>", encoding="utf-8")
    (ui / "assets" / "app.js").write_text("export const ready = true", encoding="utf-8")
    (tmp_path / "private.txt").write_text("private", encoding="utf-8")
    app = make_app(tmp_path, monkeypatch, ui=ui)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/workbench", follow_redirects=False).status_code == 307
        assert "Workbench" in client.get("/workbench/research/new").text
        asset = client.get("/workbench/assets/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert client.get("/workbench/assets/missing.js").status_code == 404
        assert client.get("/workbench/%2e%2e/private.txt").status_code == 404


def test_market_runtime_is_injected_and_lazy_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    provider = Provider(frame(pd.date_range("2026-10-19", "2026-10-23", freq="B", tz=ET)))
    config = deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path / "state")
    app = create_app(
        config, market_provider=provider, market_clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    , authentication_required=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        capabilities = client.get("/api/v1/bootstrap").json()["capabilities"]
        assert next(row for row in capabilities if row["id"] == "charts")["state"] == "available"
        assert client.get("/api/v1/market/capabilities").json()["realtime_guaranteed"] is False
        assert provider.calls == []
        result = client.get("/api/v1/market/bars", params={"symbol": "SPY"})
        assert result.status_code == 200
        assert result.json()["meta"]["source"] == "fixture"
        assert result.json()["meta"]["feed"] == "fixture-bars"
        assert len(result.json()["bars"]) == 5
        assert len(provider.calls) == 1
        assert client.get("/api/v1/market/bars", params={"symbol": "BTC-USD"}).status_code == 422
