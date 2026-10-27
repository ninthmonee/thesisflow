"""Configured research credentials reach vendors without entering public run state."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tradingagents.dataflows.credentials import data_credentials_scope
from tradingagents.dataflows.vendors.fred import get_api_key
from tradingagents.web.api.research_data_schemas import ResearchDataConnectionError
from tradingagents.web.application.runtime import create_runtime_resolver
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app


def test_settings_api_freezes_references_and_private_runtime_consumes_ui_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "ambient-key-must-not-win")
    secrets = MemorySecretStore()
    config = {
        "results_dir": str(tmp_path / "state"),
        "llm_provider": "ollama",
        "quick_think_provider": "ollama",
        "deep_think_provider": "ollama",
    }
    app = create_app(config, secret_store=secrets)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = {"Origin": "http://127.0.0.1:8000"}
        created = client.post(
            "/api/v1/research-data-connections",
            json={"provider": "fred", "name": "FRED account", "api_key": "configured-test-key"},
            headers=headers,
        )
        assert created.status_code == 201
        connection = created.json()
        public = client.get("/api/v1/research-data-connections")
        assert "configured-test-key" not in public.text and "credential_ref" not in public.text
        response = client.post(
            "/api/v1/runs",
            json={"ticker": "NVDA", "date": "2026-10-23", "analysts": ["market"]},
            headers=headers,
        )
        assert response.status_code == 202
        manifest_response = client.get(f"/api/v1/runs/{response.json()['id']}/manifest")
        assert manifest_response.status_code == 200
        manifest = manifest_response.json()
        assert manifest["data_connections"]["providers"]["fred"]["connection_id"] == connection["id"]
        assert "configured-test-key" not in json.dumps(manifest)
        assert "ambient-key-must-not-win" not in json.dumps(manifest)
        assert b"configured-test-key" not in app.state.database.path.read_bytes()
        runtime = create_runtime_resolver(app.state.database, config, secret_store=secrets)(manifest)
        resolver = runtime.pop("__data_connection_resolver")
        assert "configured-test-key" not in str(runtime)
        with data_credentials_scope(resolver):
            assert get_api_key() == "configured-test-key"
        with app.state.store.data_connection_context_provider(manifest["data_connections"]):
            assert get_api_key() == "configured-test-key"
        cleared = client.post(
            f"/api/v1/research-data-connections/{connection['id']}/clear",
            json={"revision": connection["revision"]},
            headers=headers,
        )
        assert cleared.status_code == 200
        with pytest.raises(ResearchDataConnectionError) as changed:
            resolver("fred")
        assert changed.value.code == "research_data_configuration_changed"
        fresh = app.state.research_data_service.build_runtime_resolver(
            app.state.research_data_service.freeze_for_run(
                manifest["workspace_id"], manifest["owner_id"]
            )
        )
        assert fresh("fred") == {}  # Explicit clear must not use the ambient key.


def test_settings_validation_does_not_echo_sensitive_input(tmp_path):
    app = create_app({"results_dir": str(tmp_path / "state")}, secret_store=MemorySecretStore())
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/v1/research-data-connections",
            json={"provider": "unsupported", "name": "test", "api_key": "private-invalid-input"},
            headers={"Origin": "http://127.0.0.1:8000"},
        )
        assert response.status_code == 422
        assert "private-invalid-input" not in response.text


def test_legacy_run_cannot_complete_or_write_decision_after_connection_change(tmp_path):
    closed = threading.Event()
    writes = []

    class Graph:
        propagator = SimpleNamespace(get_graph_args=lambda **kwargs: {})

        def create_run_state(self, *args):
            return {}

        def begin_checkpoint(self, *args):
            return None

        def checkpoint_input(self, state):
            return state

        def stream_run(self, initial, **kwargs):
            app.state.research_data_service.update_connection(
                connection.id, {"revision": connection.revision, "enabled": False}
            )
            # A tool/node might swallow a configuration exception and yield a decision.
            yield None, {"final_trade_decision": "BUY"}

        def save_reports(self, *args, **kwargs):
            writes.append("report")

        def record_decision(self, *args):
            writes.append("decision")

        def end_checkpoint(self):
            closed.set()

    app = create_app(
        {
            "results_dir": str(tmp_path / "state"),
            "llm_provider": "ollama",
            "quick_think_provider": "ollama",
            "deep_think_provider": "ollama",
        },
        graph_factory=lambda *args, **kwargs: Graph(),
        secret_store=MemorySecretStore(),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        connection = app.state.research_data_service.create_connection(
            {"provider": "fred", "name": "FRED", "api_key": "isolated-test-only"}
        )
        response = client.post(
            "/api/runs",
            json={"ticker": "NVDA", "date": "2026-10-23", "analysts": ["market"]},
            headers={"Origin": "http://127.0.0.1:8000"},
        )
        assert response.status_code == 202
        assert closed.wait(timeout=5)
        snapshot = app.state.store.snapshot(response.json()["id"])
        assert snapshot["status"] == "failed"
        assert "配置已变更" in snapshot["error"]
        assert writes == []
