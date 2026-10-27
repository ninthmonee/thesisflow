"""Versioned contracts are explicit and can be generated without runtime side effects."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tradingagents.web.api import create_api_router, create_schema_app
from tradingagents.web.api.router import build_bootstrap
from tradingagents.web.api.schemas import CreateResearchRequest, EventEnvelope

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_picks_only_safe_fields_and_does_not_claim_connectivity():
    app = FastAPI()
    app.include_router(create_api_router(
        public_config_reader=lambda: {
            "version": "test", "today": "2026-10-24", "provider": "deepseek",
            "quick_provider": "deepseek", "deep_provider": "anthropic",
            "quick_model": "quick-model", "deep_model": "deep-model",
            "model_ready": True, "output_language": "Chinese",
            "api_key": "DO-NOT-SERIALIZE", "backend_url": "https://private.invalid",
            "services": [{"id": "model", "name": "模型服务", "configured": True,
                          "status": "configured", "api_key": "DO-NOT-SERIALIZE"}],
        },
        workspace_reader=lambda: {"id": "space-1", "owner_id": "owner-1", "name": "研究空间",
                                  "secret": "DO-NOT-SERIALIZE"},
    ))
    with TestClient(app) as client:
        response = client.get("/api/v1/bootstrap")
        assert response.status_code == 200
        payload = response.json()
        assert payload["workspace"] == {"id": "space-1", "owner_id": "owner-1", "name": "研究空间"}
        assert payload["model"]["deep_provider"] == "anthropic"
        assert payload["model"]["configured"] is True
        assert payload["model"]["verification"] == "not_checked"
        assert payload["defaults"]["analysts"] == ["market", "social", "news", "fundamentals"]
        states = {item["id"]: item["state"] for item in payload["capabilities"]}
        assert states["research"] == "available"
        assert states["model_connections"] == "planned"
        assert states["watchlists"] == "planned"
        assert "DO-NOT-SERIALIZE" not in response.text
        assert "private.invalid" not in response.text
        assert client.get("/api/v1/health").json() == {
            "status": "ok", "api_version": "1", "mode": "local",
        }


def test_missing_model_and_future_capability_override_are_separate():
    payload = build_bootstrap({}, capability_states={"watchlists": "available"})
    assert payload.model.configured is False
    assert payload.model.status == "missing"
    assert payload.workspace.id == "local-workspace"
    states = {item.id: item.state for item in payload.capabilities}
    assert states["research"] == "configuration_required"
    assert states["watchlists"] == "available"


def test_schema_app_registers_run_and_event_contracts_without_fake_operations():
    schema = create_schema_app().openapi()
    assert {"/api/v1/health", "/api/v1/bootstrap"} <= schema["paths"].keys()
    assert schema["paths"]["/api/v1/runs"]["post"]["operationId"] == "createResearchRun"
    assert schema["paths"]["/api/v1/bootstrap"]["get"]["operationId"] == "getBootstrap"
    models = schema["components"]["schemas"]
    assert {"CreateResearchRequest", "RunSnapshot", "EventEnvelope"} <= models.keys()
    assert models["EventEnvelope"]["discriminator"]["propertyName"] == "type"

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                prefix = "#/components/schemas/"
                assert value["$ref"].startswith(prefix)
                assert value["$ref"][len(prefix):] in models
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(schema)


def test_run_inputs_keep_legacy_fields_and_reject_unsafe_values():
    request = CreateResearchRequest(ticker="nvda", date="2026-10-24")
    assert request.ticker == "NVDA"
    assert request.depth == 1
    for patch in ({"depth": True}, {"ticker": "../../secret"}, {"date": "2026-02-30"},
                  {"analysts": ["market", "market"]}, {"api_key": "private"}):
        with pytest.raises(ValidationError):
            CreateResearchRequest.model_validate({"ticker": "NVDA", "date": "2026-10-24", **patch})


def test_event_discriminator_checks_payload_and_forbids_private_extra_fields():
    data = {
        "event_id": "1", "seq": 1, "run_id": str(uuid4()), "attempt_id": str(uuid4()),
        "timestamp": "2026-10-24T10:00:00Z", "schema_version": "1", "type": "progress",
        "payload": {"stage": "analysts", "label": "分析中"},
    }
    assert EventEnvelope.model_validate(data).root.type == "progress"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate({**data, "payload": {"status": "running"}})
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate({**data, "api_key": "private"})


def test_offline_export_never_imports_runtime_or_calls_dotenv(tmp_path):
    output = tmp_path / "openapi.json"
    script = ROOT / "scripts" / "export_openapi.py"
    code = """
import runpy, socket, sys, types
def blocked(*args, **kwargs):
    raise AssertionError('Runtime side effect during contract export')
dotenv = types.ModuleType('dotenv')
dotenv.find_dotenv = blocked
dotenv.load_dotenv = blocked
sys.modules['dotenv'] = dotenv
socket.create_connection = blocked
sys.argv = [sys.argv[1], '--output', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
assert 'tradingagents' not in sys.modules
assert 'tradingagents.web.server' not in sys.modules
"""
    completed = subprocess.run([sys.executable, "-c", code, str(script), str(output)],
                               cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text())["openapi"] == "3.1.0"
    assert list(tmp_path.iterdir()) == [output]


def test_export_check_detects_drift_without_rewriting(tmp_path):
    spec = importlib.util.spec_from_file_location("web_contract_export", ROOT / "scripts/export_openapi.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canonical = module.canonical_contract()
    assert canonical == module.canonical_contract()
    assert (ROOT.parent / "contracts/openapi.json").read_text(encoding="utf-8") == canonical

    target = tmp_path / "schema.json"
    target.write_text("{}\n")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/export_openapi.py"),
                             "--check", "--output", str(target)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert target.read_text() == "{}\n"
