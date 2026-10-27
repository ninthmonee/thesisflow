"""Frozen role snapshots resolve explicit credentials only in private worker runtime."""

from unittest.mock import Mock

import pytest

from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.api.schemas import CreateResearchRequest
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.application.runtime import create_runtime_resolver, create_selection_resolver
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.recovery_policy import POLICY_FILE
from tradingagents.web.infrastructure.secrets import MemorySecretStore


def test_selection_uses_existing_local_only_when_no_template_exists(tmp_path):
    database = initialize_database(tmp_path / "models.sqlite3")
    service = ModelConfigurationService(database, database.local_workspace(), secret_store=MemorySecretStore())
    resolve = create_selection_resolver(service, lambda: {"model_ready": True})
    body = CreateResearchRequest(ticker="NVDA", date="2026-01-01")
    assert resolve(body)["mode"] == "local"
    with pytest.raises(ModelConfigurationError):
        resolve(body.model_copy(update={"template_version_id": "missing-template"}))
    unavailable = create_selection_resolver(service, lambda: {"model_ready": False})
    with pytest.raises(ModelConfigurationError):
        unavailable(body)
    database.dispose()


def test_restored_store_cannot_silently_reuse_ambient_authentication(tmp_path):
    database = initialize_database(tmp_path / "models.sqlite3")
    workspace = database.local_workspace()
    service = ModelConfigurationService(database, workspace, secret_store=MemorySecretStore())
    (tmp_path / POLICY_FILE).write_text('{"legacy_local_auth_disabled":true}')
    status = Mock(return_value={"model_ready": True})
    selection = create_selection_resolver(service, status)
    with pytest.raises(ModelConfigurationError, match="显式绑定"):
        selection(CreateResearchRequest(ticker="NVDA", date="2026-01-01"))
    status.assert_not_called()
    resolver = create_runtime_resolver(database, {}, secret_store=MemorySecretStore())
    with pytest.raises(ModelConfigurationError, match="不能自动复用"):
        resolver({"workspace_id": workspace.workspace_id, "owner_id": workspace.user_id,
                  "runtime": {}, "request": {}, "selection": {"mode": "local"}})
    database.dispose()


def test_runtime_uses_frozen_models_and_never_serializes_keys(tmp_path, monkeypatch):
    database = initialize_database(tmp_path / "models.sqlite3")
    workspace = database.local_workspace()
    secrets = MemorySecretStore()
    service = ModelConfigurationService(database, workspace, secret_store=secrets,
                                        tester=lambda snapshot, key: {"chat": True, "tools": "supported"})
    conn = service.create_connection({"name": "Research", "provider": "deepseek", "api_key": "PRIVATE-KEY"})
    item = service.create_profile({"connection_id": conn["id"], "model_id": "test-model"})
    template = service.save_settings({"default_profile_id": item["id"]})
    select = create_selection_resolver(service)
    body = CreateResearchRequest(ticker="NVDA", date="2026-01-01", depth=2)
    with pytest.raises(ModelConfigurationError):
        select(body)
    service.test_profile(item["id"], 1)
    selected = select(body)
    assert selected["model_snapshot"]["template_version_id"] == template["id"]
    assert "PRIVATE-KEY" not in str(selected)
    manifest = {"workspace_id": workspace.workspace_id, "owner_id": workspace.user_id,
                "runtime": {"max_tokens": 100, "api_key": "FORBIDDEN", "data_vendors": {"news": "fixed"}},
                "request": body.model_dump(), "selection": selected}
    baseline = {"max_tokens": 500, "data_vendors": {"news": "old"}}
    callbacks = [object()]
    factory = Mock(return_value=Mock(get_llm=lambda: "role-model"))
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    resolve = create_runtime_resolver(database, baseline, secret_store=secrets)
    config = resolve(manifest, callbacks=callbacks)
    assert config["max_tokens"] == 100 and "api_key" not in config
    assert config["max_debate_rounds"] == 2 and config["output_language"] == "Chinese"
    assert baseline == {"max_tokens": 500, "data_vendors": {"news": "old"}}
    assert config["__role_model_resolver"]("news") == "role-model"
    assert factory.call_args.kwargs["api_key"] == "PRIVATE-KEY"
    assert factory.call_args.kwargs["callbacks"] is callbacks
    assert config["__role_model_signature"] == selected["model_snapshot"]["signature"]
    with pytest.raises(ModelConfigurationError):
        resolve({**manifest, "selection": {"mode": "configured"}})
    with pytest.raises(ModelConfigurationError):
        resolve({**manifest, "selection": {"mode": "configured", "model_snapshot": None}})
    database.dispose()
