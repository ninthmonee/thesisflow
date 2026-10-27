"""Model settings persist while raw credentials and user scopes stay isolated."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tradingagents.web.api import create_api_router
from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import ModelConnection, User, Workspace
from tradingagents.web.infrastructure.secrets import (
    KeyringSecretStore,
    MemorySecretStore,
    SecretStoreError,
)
from tradingagents.web.infrastructure.seeds import LocalWorkspace


@pytest.fixture
def models(tmp_path):
    database = initialize_database(tmp_path / "models.sqlite3")
    workspace = database.local_workspace()
    secrets = MemorySecretStore()
    tester = Mock(return_value={"chat": True, "tools": "supported"})
    service = ModelConfigurationService(
        database,
        workspace,
        secret_store=secrets,
        tester=tester,
        discoverer=lambda snapshot, key: ["model-b", "model-a"],
    )
    yield service, database, secrets, tester
    database.dispose()


def connection(service, *, name="DeepSeek", key="secret-one", provider="deepseek"):
    return service.create_connection({"name": name, "provider": provider, "api_key": key})


def profile(service, connection_id):
    return service.create_profile(
        {"connection_id": connection_id, "model_id": "test-model", "parameters": {}}
    )


def test_connections_persist_without_plaintext_and_blank_key_keeps_secret(models):
    service, database, secrets, _ = models
    first = connection(service)
    second = connection(service, name="Other account", key="secret-two")
    with database.session() as session:
        first_ref = session.get(ModelConnection, first["id"]).credential_ref
        second_ref = session.get(ModelConnection, second["id"]).credential_ref
    assert first_ref != second_ref
    assert secrets.get(first_ref) == "secret-one"
    assert secrets.get(second_ref) == "secret-two"
    changed = service.update_connection(
        first["id"], {"revision": first["revision"], "api_key": "", "name": "renamed"}
    )
    assert changed["name"] == "renamed"
    assert changed["credential_mask"] == "••••••••"
    assert secrets.get(first_ref) == "secret-one"
    fresh = ModelConfigurationService(database, service.workspace, secret_store=secrets)
    assert len(fresh.list_connections()) == 2
    assert b"secret-one" not in database.path.read_bytes()
    assert "credential_ref" not in changed and "api_key" not in changed
    with pytest.raises(ModelConfigurationError, match="配置已更新"):
        service.update_connection(first["id"], {"revision": 1, "name": "lost edit"})


def test_no_plaintext_fallback_when_system_store_fails(models):
    service, database, _, _ = models
    broken = Mock()
    broken.set.side_effect = SecretStoreError("raw private backend error")
    service.secrets = broken
    with pytest.raises(ModelConfigurationError) as error:
        connection(service)
    assert error.value.status == 503
    assert "raw private" not in str(error.value)
    with database.session() as session:
        assert session.scalar(select(ModelConnection.id)) is None


def test_keyring_rejects_plaintext_or_unknown_backend(monkeypatch):
    class FileBackend:
        __module__ = "keyrings.alt.file"

        def set_password(self, *args):
            raise AssertionError("plaintext backend must not be called")

    monkeypatch.setattr("keyring.get_keyring", lambda: FileBackend())
    with pytest.raises(SecretStoreError):
        KeyringSecretStore().set("reference", "private")


def test_profile_probe_updates_status_and_edit_invalidates_it(models):
    service, _, _, tester = models
    conn = connection(service)
    item = profile(service, conn["id"])
    assert item["test_status"] == "untested"
    passed = service.test_profile(item["id"], item["revision"])
    assert passed["success"] and passed["profile"]["test_status"] == "passed"
    assert tester.call_args.args[1] == "secret-one"
    assert passed["profile"]["capabilities"] == {"chat": True, "tools": "supported"}
    changed = service.update_profile(
        item["id"], {"revision": passed["profile"]["revision"], "model_id": "new-model"}
    )
    assert changed["test_status"] == "untested" and changed["tested_at"] is None
    with pytest.raises(ModelConfigurationError):
        service.test_profile(item["id"], 1)
    tester.side_effect = RuntimeError("secret-one https://private.invalid/endpoint")
    failed = service.test_profile(item["id"], changed["revision"])
    assert not failed["success"] and failed["profile"]["test_status"] == "failed"
    assert "secret-one" not in str(failed) and "private.invalid" not in str(failed)


def test_connection_rotation_invalidates_profiles_and_keeps_frozen_credential_reference(models):
    service, database, secrets, _ = models
    conn = connection(service)
    item = profile(service, conn["id"])
    service.test_profile(item["id"], item["revision"])
    service.save_settings({"default_profile_id": item["id"]})
    snapshot = service.resolve_model_snapshot()
    old_ref = snapshot["roles"]["market"]["credential_ref"]
    service.update_connection(conn["id"], {"revision": conn["revision"], "api_key": "new-secret"})
    assert service.list_profiles()[0]["test_status"] == "untested"
    assert secrets.get(old_ref) == "secret-one"
    with database.session() as session:
        assert session.get(ModelConnection, conn["id"]).credential_ref != old_ref
    with pytest.raises(ModelConfigurationError):
        service.resolve_model_snapshot()


def test_settings_are_versioned_and_resolve_all_roles_with_overrides(models):
    service, _, _, _ = models
    first, second = (
        connection(service),
        connection(service, name="Claude", key="secret-two", provider="anthropic"),
    )
    a, b = profile(service, first["id"]), profile(service, second["id"])
    service.test_profile(a["id"], 1)
    service.test_profile(b["id"], 1)
    v1 = service.save_settings({"default_profile_id": a["id"], "role_bindings": {"news": b["id"]}})
    v2 = service.save_settings({"expected_version": 1, "default_profile_id": b["id"]})
    assert v1["version"] == 1 and v2["version"] == 2 and v1["id"] != v2["id"]
    assert len(service.list_templates()) == 2
    snapshot = service.resolve_model_snapshot(v1["id"])
    assert len(snapshot["roles"]) == 14
    assert snapshot["roles"]["news"]["profile_id"] == b["id"]
    assert snapshot["roles"]["market"]["profile_id"] == a["id"]
    assert snapshot["roles"]["deep"]["profile_id"] == a["id"]
    assert "secret-one" not in str(snapshot)
    assert service.resolve_model_snapshot(v1["id"])["signature"] == snapshot["signature"]
    with pytest.raises(ModelConfigurationError):
        service.save_settings({"expected_version": 1, "default_profile_id": a["id"]})
    with pytest.raises(ModelConfigurationError) as error:
        service.delete_connection(first["id"], first["revision"])
    assert error.value.code == "connection_in_use"


def test_scope_prevents_cross_workspace_read_and_write(models):
    service, database, secrets, _ = models
    conn = connection(service)
    item = profile(service, conn["id"])
    with database.session() as session:
        session.add(User(id="other-user", name="Other"))
        session.flush()
        session.add(Workspace(id="other-workspace", owner_id="other-user", name="Other"))
    other = ModelConfigurationService(
        database, LocalWorkspace("other-user", "other-workspace", "", "Other"), secret_store=secrets
    )
    assert other.list_connections() == [] and other.list_profiles() == []
    for method, args in (
        (other.update_connection, (conn["id"], {"revision": 1, "enabled": False})),
        (other.create_profile, ({"connection_id": conn["id"], "model_id": "bad"},)),
        (other.test_profile, (item["id"], 1)),
    ):
        with pytest.raises(ModelConfigurationError) as error:
            method(*args)
        assert error.value.status == 404


def test_model_discovery_and_parameter_validation(models):
    service, _, _, _ = models
    conn = connection(service)
    assert service.discover_models(conn["id"])["models"] == [{"id": "model-a"}, {"id": "model-b"}]
    claude = connection(service, name="Claude", provider="anthropic")
    assert not service.discover_models(claude["id"])["supported"]
    with pytest.raises(ModelConfigurationError):
        service.create_profile(
            {"connection_id": conn["id"], "model_id": "x", "parameters": {"api_key": "bad"}}
        )


def test_http_crud_errors_and_secret_outputs(models):
    service, _, _, _ = models
    app = FastAPI()
    app.include_router(create_api_router(model_service_reader=lambda: service))
    with TestClient(app) as client:
        assert len(client.get("/api/v1/model-providers").json()["providers"]) >= 6
        created = client.post(
            "/api/v1/model-connections",
            json={"name": "Test", "provider": "deepseek", "api_key": "raw-secret"},
        )
        assert created.status_code == 201 and "raw-secret" not in created.text
        item = created.json()
        mismatch = client.patch(
            f"/api/v1/model-connections/{item['id']}", json={"revision": 999, "enabled": False}
        )
        assert mismatch.status_code == 409 and mismatch.json()["error_code"] == "revision_conflict"
        bad = client.post(
            "/api/v1/model-connections",
            json={
                "name": "bad",
                "provider": "openai",
                "api_key": "x",
                "endpoint": "https://user:password@example.com/v1",
            },
        )
        assert bad.status_code == 422
        deleted = client.delete(
            f"/api/v1/model-connections/{item['id']}?revision={item['revision']}"
        )
        assert deleted.status_code == 204


def test_frozen_resolver_passes_explicit_key_without_mutating_environment(models, monkeypatch):
    service, _, _, _ = models
    conn = connection(service)
    item = profile(service, conn["id"])
    service.test_profile(item["id"], 1)
    service.save_settings({"default_profile_id": item["id"]})
    snapshot = service.resolve_model_snapshot()
    before = deepcopy(snapshot)
    calls = []

    def fake_factory(provider, model, **kwargs):
        calls.append((provider, model, kwargs))
        return Mock(get_llm=lambda: "configured-model")

    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", fake_factory)
    resolver = service.build_role_model_resolver(snapshot)
    assert resolver("market") == resolver("news") == "configured-model"
    assert len(calls) == 1 and calls[0][2]["api_key"] == "secret-one"
    assert snapshot == before


@pytest.mark.parametrize("tool_capability", ["not_checked", "unsupported", False])
def test_unverified_tools_block_enabled_analysts_but_chat_still_passes(models, tool_capability):
    service, _, _, tester = models
    tester.return_value = {"chat": True, "tools": tool_capability}
    conn = connection(service)
    item = profile(service, conn["id"])
    result = service.test_profile(item["id"], 1)
    assert result["success"] and result["profile"]["test_status"] == "passed"
    assert "聊天检测通过" in result["message"]
    service.save_settings({"default_profile_id": item["id"]})
    with pytest.raises(ModelConfigurationError) as caught:
        service.resolve_model_snapshot()
    assert caught.value.code == "model_tools_not_verified" and caught.value.status == 409
    assert service.resolve_profile_snapshot(item["id"])["profile_id"] == item["id"]


def test_chat_only_debate_and_inactive_analyst_models_do_not_require_tools(models):
    service, _, _, tester = models
    conn = connection(service)
    tool_model, chat_model = profile(service, conn["id"]), profile(service, conn["id"])
    service.test_profile(tool_model["id"], 1)
    tester.return_value = {"chat": True, "tools": "unsupported"}
    service.test_profile(chat_model["id"], 1)
    service.save_settings(
        {
            "default_profile_id": tool_model["id"],
            "analysts": ["market"],
            "role_bindings": {"news": chat_model["id"], "research_manager": chat_model["id"]},
        }
    )
    snapshot = service.resolve_model_snapshot()
    assert snapshot["roles"]["research_manager"]["profile_id"] == chat_model["id"]
    assert snapshot["roles"]["market"]["capabilities"]["tools"] == "supported"
    assert "news" not in snapshot["roles"]
    # The request can enable an analyst absent from the template's defaults.
    from tradingagents.web.api.schemas import CreateResearchRequest
    from tradingagents.web.application.runtime import create_selection_resolver

    with pytest.raises(ModelConfigurationError) as caught:
        create_selection_resolver(service)(
            CreateResearchRequest(
                ticker="NVDA",
                date="2026-01-01",
                analysts=["news"],
            )
        )
    assert caught.value.code == "model_tools_not_verified"


def test_selected_social_role_does_not_require_tool_capability(models):
    service, _database, _secrets, tester = models
    conn = connection(service)
    item = profile(service, conn["id"])
    tester.return_value = {"chat": True, "tools": "unsupported"}
    service.test_profile(item["id"], 1)
    service.save_settings({"default_profile_id": item["id"], "analysts": ["social"]})
    snapshot = service.resolve_model_snapshot()
    assert set(snapshot["roles"]) == {
        "social",
        "bull",
        "bear",
        "research_manager",
        "trader",
        "risk_aggressive",
        "risk_conservative",
        "risk_neutral",
        "portfolio_manager",
        "quick",
        "deep",
    }
    assert snapshot["roles"]["social"]["capabilities"]["tools"] == "unsupported"
    assert snapshot["roles"]["quick"] == snapshot["roles"]["deep"]


def test_unused_analyst_bad_probe_does_not_block_selected_roles(models):
    service, _database, _secrets, tester = models
    conn = connection(service)
    active, unused = profile(service, conn["id"]), profile(service, conn["id"])
    service.test_profile(active["id"], 1)
    service.save_settings(
        {
            "default_profile_id": active["id"],
            "analysts": ["market"],
            "role_bindings": {"news": unused["id"]},
        }
    )
    result = service.resolve_model_snapshot(analysts=["market"])
    assert "news" not in result["roles"]
    assert result["roles"]["market"]["profile_id"] == active["id"]
    with pytest.raises(ModelConfigurationError) as untested:
        service.resolve_model_snapshot(analysts=["news"])
    assert untested.value.code == "model_not_tested" and "news" in str(untested.value)


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"name": "verify_model_connection", "args": {"value": 7}}, "supported"),
        ({"name": "other_tool", "args": {"value": 7}}, "unsupported"),
        ({"name": "verify_model_connection", "args": {"value": 7.0}}, "unsupported"),
        (None, "unsupported"),
        (RuntimeError("PRIVATE-KEY https://private.invalid"), "unsupported"),
    ],
)
def test_default_tester_probes_an_actual_bound_tool_without_executing_it(
    monkeypatch, reply, expected
):
    model = Mock()
    model.invoke.return_value = Mock(content="OK")
    if isinstance(reply, Exception):
        model.bind_tools.return_value.invoke.side_effect = reply
    else:
        model.bind_tools.return_value.invoke.return_value = Mock(
            tool_calls=[] if reply is None else [reply]
        )
    factory = Mock(return_value=Mock(get_llm=lambda: model))
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    result = ModelConfigurationService._test_model(
        {
            "provider": "deepseek",
            "model": "test-model",
            "base_url": "https://model.invalid/v1",
            "parameters": {"max_tokens": 100, "timeout": 120, "max_retries": 4},
        },
        "explicit-key",
    )
    assert result == {"chat": True, "tools": expected, "structured_output": "not_checked"}
    tool = model.bind_tools.call_args.args[0][0]["function"]
    assert tool["name"] == "verify_model_connection"
    assert tool["parameters"]["properties"]["value"]["type"] == "integer"
    assert "value 7" in model.bind_tools.return_value.invoke.call_args.args[0]
    assert factory.call_args.kwargs["api_key"] == "explicit-key"
    assert factory.call_args.kwargs["max_tokens"] == 100
    assert factory.call_args.kwargs["timeout"] == 30
    assert factory.call_args.kwargs["max_retries"] == 0
    assert "PRIVATE-KEY" not in str(result)
