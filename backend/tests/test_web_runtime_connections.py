"""Runtime registry boundaries use temporary accounts, programs and fake probes."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from tradingagents.web.api.runtime_router import create_runtime_router
from tradingagents.web.api.runtime_schemas import RuntimeConfigurationError
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.application.runtime_connections import RuntimeConnectionService
from tradingagents.web.infrastructure.backup_bundle import create_bundle, restore_bundle
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, ModelConnection, User, Workspace
from tradingagents.web.infrastructure.recovery_policy import POLICY_FILE
from tradingagents.web.infrastructure.runtime_models import AgentRuntimeConnection
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.infrastructure.seeds import LocalWorkspace


class RecordingSecrets(MemorySecretStore):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, reference):
        self.reads.append(reference)
        return super().get(reference)


def ready_probe(_options):
    return {"program_version": "0.164.0", "auth_status": "authorized", "auth_fingerprint": "a" * 64,
        "models": [{"id": "test-runtime-model", "label": "Test model"}],
        "capabilities": {"dynamic_tools": True, "restricted_tools": True, "cancellation": True}}


@pytest.fixture
def runtime_site(tmp_path):
    database = initialize_database(tmp_path / "state" / "workbench.sqlite3")
    owner = database.local_workspace()
    friend = LocalWorkspace(str(uuid4()), str(uuid4()), "", "Friend")
    with database.session() as session:
        session.add(User(id=friend.user_id, name="Friend"))
        session.flush()
        session.add(Workspace(id=friend.workspace_id, owner_id=friend.user_id, name="Friend"))
        session.flush()
        session.add(Membership(workspace_id=friend.workspace_id, user_id=friend.user_id, role="owner"))
    executable = tmp_path / "codex"
    executable.write_text("#!/usr/bin/env python3\nraise RuntimeError('fixture must never execute')\n")
    executable.chmod(0o700)
    secrets = RecordingSecrets()
    service = RuntimeConnectionService(database, owner, secret_store=secrets, probe=ready_probe)
    stranger = RuntimeConnectionService(database, friend, secret_store=secrets, probe=lambda _: pytest.fail("foreign probe"))
    yield database, owner, service, stranger, executable, secrets
    database.dispose()


def create(service, executable, **overrides):
    return service.create_connection({"name": "Local research", "model_id": "test-runtime-model",
        "executable_path": str(executable), **overrides})


def enable_ready(service, connection):
    tested = service.test_connection(connection["id"], connection["revision"])
    assert tested["success"]
    return service.update_connection(connection["id"], {"revision": tested["connection"]["revision"], "enabled": True})


def configured_model(service, secret="fake-runtime-key"):
    connection = service.models.create_connection({"name": "Private Responses", "provider": "custom",
        "api_protocol": "openai_responses", "endpoint": "https://models.example/v1", "api_key": secret})
    profile = service.models.create_profile({"connection_id": connection["id"], "model_id": "test-runtime-model"})
    service.models.tester = lambda *_: {"chat": True, "tools": "supported"}
    result = service.models.test_profile(profile["id"], profile["revision"])
    return connection, result["profile"]


def test_connections_default_disabled_and_publish_only_safe_probe_fields(runtime_site):
    database, _owner, service, _, executable, secrets = runtime_site
    captured = []

    def probe(options):
        captured.append(options)
        return {**ready_probe(options), "raw_token": "never-publish", "message": "secret upstream error"}

    service.probe = probe
    connection = create(service, executable)
    assert not connection["enabled"] and connection["readiness"] == "not_checked"
    assert "CODEX_HOME=" in connection["login_command"] and "keyring" in connection["login_command"]
    tested = service.test_connection(connection["id"], 1)
    assert tested["success"] and tested["connection"]["revision"] == 2
    public = json.dumps(tested, default=str)
    assert "a" * 64 not in public and "never-publish" not in public and "secret upstream error" not in public
    assert not secrets.reads
    home = database.path.parent / "runtime-auth" / connection["id"]
    assert home.is_dir() and home.stat().st_mode & 0o777 == 0o700
    assert captured[0]["auth_options"]["runtime_home"] == str(home)
    assert not (home / "auth.json").exists() and not (home / "config.toml").exists()


def test_unimplemented_types_and_foreign_accounts_do_not_gain_host_access(runtime_site):
    database, _owner, service, stranger, executable, secrets = runtime_site
    connection = create(service, executable)
    assert {row["id"] for row in service.list_types() if row["implemented"]} == {"codex"}
    assert all(not row["host_access"] for row in stranger.list_types())
    assert stranger.list_connections() == []
    with pytest.raises(RuntimeConfigurationError) as error:
        create(stranger, executable)
    assert error.value.status_code == 403
    for operation in (
        lambda: stranger.test_connection(connection["id"], 1),
        lambda: stranger.update_connection(connection["id"], {"revision": 1, "name": "stolen"}),
        lambda: stranger.delete_connection(connection["id"], 1),
    ):
        with pytest.raises(RuntimeConfigurationError) as denied:
            operation()
        assert denied.value.status_code == 403
    assert not secrets.reads
    with database.session() as session:
        session.execute(delete(Membership).where(Membership.workspace_id == stranger.workspace.workspace_id))
    with pytest.raises(RuntimeConfigurationError):
        stranger.list_connections()


def test_freeze_requires_enabled_current_test_and_snapshot_is_bound_to_owner(runtime_site):
    _database, owner, service, stranger, executable, _secrets = runtime_site
    connection = create(service, executable)
    with pytest.raises(RuntimeConfigurationError, match="尚未启用"):
        service.freeze_for_run(connection["id"])
    connection = enable_ready(service, connection)
    snapshot = service.freeze_for_run(connection["id"])
    assert snapshot["id"] == connection["id"] and snapshot["owner_id"] == owner.user_id
    assert snapshot["transport"] == "stdio" and snapshot["execution_host"] == "server_local"
    assert "executable_path" not in snapshot and "auth_fingerprint" not in snapshot
    service.validate_snapshot(snapshot)
    options = service.runtime_options(snapshot)
    assert options["executable_path"] == str(executable)
    assert options["auth_options"]["expected_auth_fingerprint"] == "a" * 64
    with pytest.raises(RuntimeConfigurationError):
        stranger.runtime_options(snapshot)
    with pytest.raises(RuntimeConfigurationError, match="校验失败"):
        service.validate_snapshot({**snapshot, "model_id": "tampered"})
    changed = service.update_connection(connection["id"], {"revision": connection["revision"], "model_id": "another-model"})
    assert changed["readiness"] == "stale"
    with pytest.raises(RuntimeConfigurationError):
        service.validate_snapshot(snapshot)


def test_program_replacement_and_configuration_conflicts_invalidate_execution(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    connection = enable_ready(service, create(service, executable))
    snapshot = service.freeze_for_run(connection["id"])
    with pytest.raises(RuntimeConfigurationError) as conflict:
        service.update_connection(connection["id"], {"revision": 1, "name": "stale"})
    assert conflict.value.code == "revision_conflict"
    executable.write_text("different fixture executable\n")
    with pytest.raises(RuntimeConfigurationError) as changed:
        service.runtime_options(snapshot)
    assert changed.value.code == "runtime_program_changed"


def test_program_replacement_during_probe_cannot_be_marked_ready(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    connection = create(service, executable)

    def replaced(options):
        executable.write_text("different program while probing\n")
        return ready_probe(options)

    service.probe = replaced
    tested = service.test_connection(connection["id"], 1)
    assert not tested["success"] and tested["connection"]["readiness"] == "failed"
    assert "程序发生变化" in tested["message"]


def test_model_profile_parameters_are_not_silently_ignored(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    _connection, profile = configured_model(service)
    service.models.update_profile(profile["id"], {"revision": profile["revision"], "parameters": {"temperature": 0.2}})
    with pytest.raises(RuntimeConfigurationError) as unsupported:
        create(service, executable, auth_mode="model_connection", model_profile_id=profile["id"])
    assert unsupported.value.code == "runtime_model_parameters_unsupported"


def test_probe_result_cannot_overwrite_a_concurrent_configuration_update(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    connection = create(service, executable)
    entered, release = Event(), Event()

    def delayed(options):
        entered.set()
        assert release.wait(5)
        return ready_probe(options)

    service.probe = delayed
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.test_connection, connection["id"], 1)
        assert entered.wait(5)
        with pytest.raises(RuntimeConfigurationError) as busy:
            service.test_connection(connection["id"], 1)
        assert busy.value.status_code == 503
        current = service.update_connection(connection["id"], {"revision": 1, "name": "new name"})
        release.set()
        with pytest.raises(RuntimeConfigurationError, match="已改变"):
            pending.result()
    assert service.list_connections()[0]["name"] == "new name"
    assert current["readiness"] == "not_checked"


def test_api_binding_uses_current_owned_responses_model_without_secret_disclosure(runtime_site):
    database, _owner, service, _, executable, secrets = runtime_site
    model_connection, profile = configured_model(service)
    calls = []
    service.probe = lambda options: calls.append(options) or ready_probe(options)
    runtime = create(service, executable, auth_mode="model_connection", model_profile_id=profile["id"])
    assert runtime["login_command"] is None
    runtime = enable_ready(service, runtime)
    assert calls[0]["auth_options"]["api_key"] == "fake-runtime-key"
    secrets.reads.clear()
    snapshot = service.freeze_for_run(runtime["id"])
    assert not secrets.reads
    serialized = json.dumps(snapshot)
    assert "fake-runtime-key" not in serialized and "credential_ref" not in serialized
    options = service.runtime_options(snapshot)
    assert options["auth_options"]["endpoint"] == "https://models.example/v1"
    assert options["auth_options"]["api_key"] == "fake-runtime-key"
    with database.session() as session:
        model = session.get(ModelConnection, model_connection["id"])
        model.enabled = False
        model.revision += 1
    assert service.list_connections()[0]["readiness"] == "stale"
    with pytest.raises(RuntimeConfigurationError):
        service.runtime_options(snapshot)


def test_chat_completions_and_foreign_models_cannot_be_bound(runtime_site):
    database, owner, service, stranger, executable, secrets = runtime_site
    model = service.models.create_connection({"name": "Chat", "provider": "deepseek", "api_key": "fake-chat-key"})
    profile = service.models.create_profile({"connection_id": model["id"], "model_id": "test-runtime-model"})
    with pytest.raises(RuntimeConfigurationError) as incompatible:
        create(service, executable, auth_mode="model_connection", model_profile_id=profile["id"])
    assert incompatible.value.code == "runtime_model_protocol_unsupported"
    other = ModelConfigurationService(database, stranger.workspace, secret_store=secrets)
    model = other.create_connection({"name": "Other", "provider": "custom", "api_protocol": "openai_responses",
        "endpoint": "https://models.example/v1", "api_key": "other-fake-key"})
    profile = other.create_profile({"connection_id": model["id"], "model_id": "test-runtime-model"})
    with pytest.raises(RuntimeConfigurationError) as foreign:
        create(service, executable, auth_mode="model_connection", model_profile_id=profile["id"])
    assert foreign.value.status_code == 404
    assert service.workspace.user_id == owner.user_id


def test_restore_disables_runtime_and_does_not_package_cli_authentication(runtime_site, tmp_path):
    database, _owner, service, _, executable, _secrets = runtime_site
    runtime = enable_ready(service, create(service, executable))
    home = database.path.parent / "runtime-auth" / runtime["id"]
    (home / "fake-local-credential").write_text("test-only-placeholder")
    bundle = tmp_path / "bundle.zip"
    create_bundle(database.path.parent, bundle)
    destination = tmp_path / "restored"
    restore_bundle(bundle, destination)
    restored = initialize_database(destination / "workbench.sqlite3")
    try:
        copied = RuntimeConnectionService(restored, restored.local_workspace(), probe=ready_probe)
        view = copied.list_connections()[0]
        assert not view["enabled"] and view["readiness"] == "stale" and view["checked_at"] is None
        assert (destination / POLICY_FILE).exists()
        assert not (destination / "runtime-auth").exists()
        result = copied.test_connection(view["id"], view["revision"])
        assert not result["success"] and "恢复" in result["message"]
    finally:
        restored.dispose()
    assert service.list_connections()[0]["enabled"]


def test_missing_auth_or_unrestricted_tool_runtime_is_not_ready(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    runtime = create(service, executable)
    service.probe = lambda options: {**ready_probe(options), "auth_status": "missing"}
    tested = service.test_connection(runtime["id"], 1)
    assert not tested["success"] and tested["connection"]["auth_status"] == "not_authorized"
    service.probe = lambda options: {**ready_probe(options), "capabilities": {"dynamic_tools": True}}
    assert not service.test_connection(runtime["id"], tested["connection"]["revision"])["success"]


def test_routes_export_safe_types_and_return_cas_errors(runtime_site):
    _database, _owner, service, _, executable, _secrets = runtime_site
    app = FastAPI()
    app.include_router(create_runtime_router(lambda: service))
    with TestClient(app) as client:
        assert client.get("/api/v1/runtime-types").status_code == 200
        response = client.post("/api/v1/runtime-connections", json={"name": "Local", "model_id": "test-runtime-model",
            "executable_path": str(executable)})
        assert response.status_code == 201, response.text
        runtime = response.json()
        assert client.post(f"/api/v1/runtime-connections/{runtime['id']}/test", json={"revision": 1}).status_code == 200
        stale = client.patch(f"/api/v1/runtime-connections/{runtime['id']}", json={"revision": 1, "enabled": True})
        assert stale.status_code == 409 and stale.json()["error_code"] == "revision_conflict"
        assert client.delete(f"/api/v1/runtime-connections/{runtime['id']}?revision=2").status_code == 204
    with service.database.session() as session:
        assert session.scalar(select(AgentRuntimeConnection)) is None


def test_invalid_runtime_inputs_never_echo_unknown_secret_fields(runtime_site):
    _database, _owner, service, _, _executable, _secrets = runtime_site
    app = FastAPI()
    app.include_router(create_runtime_router(lambda: service))
    with TestClient(app) as client:
        response = client.post("/api/v1/runtime-connections", json={
            "name": "Invalid", "model_id": "model", "env": {"TOKEN": "fake-do-not-echo-secret"}})
        assert response.status_code == 422
        assert "fake-do-not-echo-secret" not in response.text
        assert response.json()["error_code"] == "invalid_runtime_request"
        response = client.post("/api/v1/runtime-connections", json={"name": "Invalid", "model_id": "model\u0000"})
        assert response.status_code == 422


def test_removed_membership_and_unsafe_home_revoke_runtime_access(runtime_site, tmp_path):
    database, owner, service, _, executable, _secrets = runtime_site
    runtime = enable_ready(service, create(service, executable))
    snapshot = service.freeze_for_run(runtime["id"])
    home = database.path.parent / "runtime-auth" / runtime["id"]
    home.rmdir()
    target = tmp_path / "outside-runtime"
    target.mkdir()
    home.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeConfigurationError, match="目录不可用"):
        service.runtime_options(snapshot)
    home.unlink()
    with database.session() as session:
        session.execute(delete(Membership).where(Membership.user_id == owner.user_id))
    with pytest.raises(RuntimeConfigurationError) as revoked:
        service.runtime_options(snapshot)
    assert revoked.value.status_code == 403
