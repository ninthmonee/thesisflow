"""Independent accounts cannot inherit host sign-in, credentials or mutable run identity."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.api.research_data_schemas import ResearchDataConnectionError
from tradingagents.web.api.schemas import CreateResearchRequest
from tradingagents.web.application.market_briefs import MarketBriefGenerator
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_data_connections import ResearchDataConnectionService
from tradingagents.web.application.runtime import create_runtime_resolver, create_selection_resolver
from tradingagents.web.domain.model_access import is_local_owner
from tradingagents.web.domain.tasks import ResearchError, TaskClaim
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, User, Workspace
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.infrastructure.seeds import LocalWorkspace
from tradingagents.web.workers.research import ResearchWorker


class RecordingSecrets(MemorySecretStore):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, reference):
        self.reads.append(reference)
        return super().get(reference)


@pytest.fixture
def accounts(tmp_path):
    database = initialize_database(tmp_path / "accounts.sqlite3")
    local, secrets = database.local_workspace(), RecordingSecrets()
    people = []
    for name in ("Alice", "Bob"):
        actor = LocalWorkspace(str(uuid4()), str(uuid4()), "", name)
        with database.session() as session:
            session.add(User(id=actor.user_id, name=name))
            session.flush()
            session.add(Workspace(id=actor.workspace_id, owner_id=actor.user_id, name=name))
            session.flush()
            session.add(Membership(workspace_id=actor.workspace_id, user_id=actor.user_id, role="owner"))
        people.append(actor)
    reader = Mock(return_value={"status": "authorized", "identity_fingerprint": "a" * 64})
    services = [ModelConfigurationService(database, actor, secret_store=secrets,
        tester=lambda *_: {"chat": True, "tools": "supported"}, local_authorization_reader=reader)
        for actor in (local, *people)]
    yield database, secrets, local, people, services, reader, tmp_path
    database.dispose()


def configured(service, secret):
    connection = service.create_connection({"name": "Personal", "provider": "deepseek", "api_key": secret})
    profile = service.create_profile({"connection_id": connection["id"], "model_id": "test-personal-model"})
    service.test_profile(profile["id"], profile["revision"])
    service.save_settings({"default_profile_id": profile["id"]})
    return connection, service.resolve_model_snapshot()


def manifest(actor, selected):
    return {"workspace_id": actor.workspace_id, "owner_id": actor.user_id, "runtime": {},
        "request": CreateResearchRequest(ticker="NVDA", date="2026-01-01").model_dump(),
        "selection": {"mode": "configured", "model_snapshot": selected}}


def test_non_local_actor_never_reads_host_status_or_subscription(accounts):
    _database, _secrets, local, people, services, reader, _root = accounts
    assert is_local_owner(local.workspace_id, local.user_id)
    assert not is_local_owner(people[0].workspace_id, local.user_id)
    service = services[1]
    assert "chatgpt" not in {row["id"] for row in service.providers()}
    assert service._local_authorization() == {"status": "unavailable"}
    assert service._local_authorization_view()["login_command"] == ""
    status = Mock(return_value={"model_ready": True})
    with pytest.raises(ModelConfigurationError) as error:
        create_selection_resolver(service, status)(CreateResearchRequest(ticker="NVDA", date="2026-01-01"))
    assert error.value.code == "model_connection_required"
    status.assert_not_called()
    reader.assert_not_called()


def test_personal_account_cannot_explicitly_create_host_subscription_connection(accounts):
    _, secrets, _, _, services, reader, _ = accounts
    with pytest.raises(ModelConfigurationError) as error:
        services[1].create_connection({"name": "Borrow host", "provider": "chatgpt", "auth_type": "oauth_local"})
    assert error.value.code == "local_model_access_forbidden"
    assert services[1].list_connections() == []
    assert secrets._values == {} and secrets.reads == []
    reader.assert_not_called()


def test_own_configured_models_resolve_only_own_keys_without_private_host_defaults(accounts, monkeypatch):
    database, secrets, _, people, services, _reader, _root = accounts
    _, first = configured(services[1], "alice-only-key")
    _, second = configured(services[2], "bob-only-key")
    factory = Mock(return_value=Mock(get_llm=lambda: "personal-model"))
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    baseline = {"api_key": "host-private-key", "backend_url": "https://host-private.invalid/v1",
        "quick_think_backend_url": "https://host-tier.invalid", "private_note": "owner-only", "max_tokens": 200}
    original = deepcopy(baseline)
    resolver = create_runtime_resolver(database, baseline, secret_store=secrets)
    for actor, selection, expected in zip(people, (first, second), ("alice-only-key", "bob-only-key"), strict=True):
        config = resolver(manifest(actor, selection))
        assert config["max_tokens"] == 200
        assert all(key not in config for key in ("api_key", "backend_url", "quick_think_backend_url", "private_note"))
        assert config["__role_model_resolver"]("market") == "personal-model"
        assert factory.call_args.kwargs["api_key"] == expected
        assert factory.call_args.kwargs["base_url"] == "https://api.deepseek.com/v1"
    assert baseline == original


def test_non_local_manifest_cannot_fall_back_to_host_credentials(accounts):
    database, secrets, _, people, _, _, _ = accounts
    resolver = create_runtime_resolver(database, {"api_key": "host-key"}, secret_store=secrets)
    document = manifest(people[0], {})
    document["selection"] = {"mode": "local"}
    with pytest.raises(ModelConfigurationError) as error:
        resolver(document)
    assert error.value.code == "local_model_access_forbidden" and secrets.reads == []


def test_foreign_or_forged_snapshot_is_rejected_before_secret_read(accounts, monkeypatch):
    _database, secrets, _, _people, services, _, _ = accounts
    _, alice = configured(services[1], "alice-only-key")
    _, bob = configured(services[2], "bob-only-key")
    secrets.reads.clear()
    factory = Mock()
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    with pytest.raises(ModelConfigurationError) as error:
        services[2].build_role_model_resolver(alice)("market")
    assert error.value.code == "credential_scope"
    tampered = deepcopy(alice)
    tampered["roles"]["market"]["credential_ref"] = bob["roles"]["market"]["credential_ref"]
    with pytest.raises(ModelConfigurationError) as error:
        services[1].build_role_model_resolver(tampered)("market")
    assert error.value.code == "model_configuration_changed"
    assert secrets.reads == []
    factory.assert_not_called()


def test_cached_client_is_invalidated_when_connection_is_disabled(accounts, monkeypatch):
    _, secrets, _, _, services, _, _ = accounts
    connection, snapshot = configured(services[1], "alice-only-key")
    factory = Mock(return_value=Mock(get_llm=lambda: "personal-model"))
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    resolve = services[1].build_role_model_resolver(snapshot)
    assert resolve("market") == "personal-model"
    secrets.reads.clear()
    services[1].update_connection(connection["id"], {"revision": connection["revision"], "enabled": False})
    with pytest.raises(ModelConfigurationError) as error:
        resolve.verify()
    assert error.value.code == "connection_disabled"
    with pytest.raises(ModelConfigurationError):
        resolve("market")
    assert secrets.reads == [] and factory.call_count == 1


@pytest.mark.parametrize("provider", ["fred", "alpha_vantage", "typesafe_jev", "sec_edgar"])
def test_new_account_cannot_import_host_research_environment(accounts, provider):
    database, secrets, _, people, _, _, _ = accounts
    environment = Mock(return_value="host-only-credential")
    data = ResearchDataConnectionService(database, people[0], secret_store=secrets, environment_reader=environment)
    with pytest.raises(ResearchDataConnectionError) as error:
        data.create_connection({"provider": provider, "name": "Host import", "credential_source": "legacy_environment"})
    assert error.value.code == "research_environment_forbidden"
    environment.assert_not_called()
    assert secrets._values == {}


def test_new_account_cannot_switch_own_data_connection_to_host_environment(accounts):
    database, secrets, _, people, _, _, _ = accounts
    environment = Mock(return_value="host-only-credential")
    data = ResearchDataConnectionService(database, people[0], secret_store=secrets, environment_reader=environment)
    row = data.create_connection({"provider": "fred", "name": "Personal FRED", "api_key": "personal-fred"})
    with pytest.raises(ResearchDataConnectionError) as error:
        data.update_connection(row.id, {"revision": row.revision, "credential_source": "legacy_environment"})
    assert error.value.code == "research_environment_forbidden"
    environment.assert_not_called()
    frozen = data.freeze_for_run(people[0].workspace_id, people[0].user_id)
    assert data.build_runtime_resolver(frozen)("fred")["api_key"] == "personal-fred"


def test_worker_checks_claim_scope_before_config_resolver_and_keeps_attempt_paths_private(accounts):
    database, secrets, _, people, services, _, root = accounts
    service = ResearchService(database, root / "research")
    external = Mock(return_value={})
    worker = ResearchWorker(service, external)
    now = datetime.now(UTC)
    claim = TaskClaim(str(uuid4()), people[0].workspace_id, people[0].user_id, str(uuid4()), 1,
        "worker-test", 1, now + timedelta(seconds=90))
    with pytest.raises(ResearchError) as error:
        worker._runtime(claim, {"workspace_id": people[1].workspace_id, "owner_id": people[1].user_id})
    assert error.value.code == "manifest_scope_mismatch"
    external.assert_not_called()
    _, selected = configured(services[1], "alice-only-key")
    worker.config_resolver = create_runtime_resolver(database, {}, secret_store=secrets)
    config, _options = worker._runtime(claim, manifest(people[0], selected))
    assert str(claim.attempt_id) in config["memory_log_path"]
    assert config["memory_log_path"].startswith(str(service.run_directory(claim.workspace_id, claim.run_id)))
    foreign_claim = replace(claim, workspace_id=people[1].workspace_id, owner_id=people[1].user_id)
    assert service.run_directory(claim.workspace_id, claim.run_id) != service.run_directory(foreign_claim.workspace_id, foreign_claim.run_id)


def test_non_local_brief_cannot_read_ambient_signin(accounts):
    _, _, _, _, services, _, _ = accounts
    status = Mock(return_value={"model_ready": True})
    generator = MarketBriefGenerator(services[1], local_status_reader=status)
    with pytest.raises(Exception) as error:
        generator._load_model()
    assert error.value.code == "market_model_required"
    status.assert_not_called()
