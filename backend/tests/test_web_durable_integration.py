"""The compatibility API and the new UI share one durable queue in workbench mode."""

from copy import deepcopy

from fastapi.testclient import TestClient

from tests.test_web_worker import OfflineGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.application.runtime import create_runtime_resolver
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app
from tradingagents.web.workers.research import ResearchWorker

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def test_legacy_and_v1_share_queue_reports_and_persistent_history(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-placeholder")
    config = deepcopy(DEFAULT_CONFIG)
    config.update(
        results_dir=str(tmp_path / "state"),
        llm_provider="openai",
        quick_think_provider=None,
        deep_think_provider=None,
    )
    app = create_app(config, secret_store=MemorySecretStore(), durable_research=True, authentication_required=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/runs", json={"ticker": "NVDA", "date": "2024-01-02"}, headers=ORIGIN
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        assert response.json()["status"] == "pending"
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "queued"
        assert app.state.store.threads == {}
        assert client.get("/api/config").json()["active_run_id"] == run_id
        worker = ResearchWorker(
            app.state.research_service,
            create_runtime_resolver(app.state.database, config),
            graph_factory=OfflineGraph,
        )
        assert worker.run_once()
        result = client.get(f"/api/v1/runs/{run_id}").json()
        assert result["status"] == "completed" and result["rating"] is None
        assert result["raw_rating"] == "Buy" and result["quality"]["status"] == "insufficient"
        assert len(result["reports"]) == 13
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        assert client.get("/api/runs").json()["runs"][0]["id"] == run_id
        assert client.get(f"/api/runs/{run_id}/report.md").status_code == 200
        exported = client.get(f"/api/v1/runs/{run_id}/report")
        assert exported.status_code == 200
        assert exported.headers["x-frame-options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in exported.headers["content-security-policy"]
        assert client.get("/api/config").json()["active_run_id"] is None
    restarted = create_app(config, secret_store=MemorySecretStore(), durable_research=True, authentication_required=False)
    with TestClient(restarted, base_url="http://127.0.0.1:8000") as client:
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "completed"
        assert client.get(f"/api/runs/{run_id}/report").status_code == 200


def test_v1_validation_does_not_echo_secret_input(tmp_path, monkeypatch):
    config = deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path)
    app = create_app(config, secret_store=MemorySecretStore(), durable_research=True, authentication_required=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/v1/runs", json={"ticker": "Bearer PRIVATE-KEY"}, headers=ORIGIN
        )
        assert response.status_code == 422
        assert response.json()["error_code"] == "validation_error"
        assert "PRIVATE-KEY" not in response.text
