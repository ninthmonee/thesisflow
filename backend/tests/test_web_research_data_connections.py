"""Research accounts are private, task-scoped and actually consumed by data tools."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from tradingagents.agents import post_screen
from tradingagents.dataflows import net
from tradingagents.dataflows.credentials import configured_credentials, data_credentials_scope
from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.vendors import fred, sec_edgar
from tradingagents.dataflows.vendors.alpha_vantage import common as alpha
from tradingagents.web.api.research_data_router import create_research_data_router
from tradingagents.web.api.research_data_schemas import (
    ResearchDataConnectionCreate,
    ResearchDataConnectionError,
)
from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_data_connections import ResearchDataConnectionService
from tradingagents.web.application.runtime import create_runtime_resolver
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, ResearchRun, User
from tradingagents.web.infrastructure.research_data_model import ResearchDataConnectionRecord
from tradingagents.web.infrastructure.secrets import MemorySecretStore, SecretStoreError
from tradingagents.web.workers.research import ResearchWorker


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "accounts.sqlite3")
    local, secrets, environment, env_reads, probes = (
        database.local_workspace(),
        MemorySecretStore(),
        {},
        [],
        [],
    )

    def read(name):
        env_reads.append(name)
        return environment.get(name)

    service = ResearchDataConnectionService(
        database,
        local,
        secret_store=secrets,
        environment_reader=read,
        tester=lambda provider, settings: probes.append((provider, dict(settings))),
    )
    yield service, database, local, secrets, environment, env_reads, probes
    database.dispose()


def create(service, provider="fred", **extra):
    fields = {
        "provider": provider,
        "name": provider,
        **(
            {"user_agent": "Research User user@example.test"}
            if provider == "sec_edgar"
            else {"api_key": "TEST-PRIVATE"}
        ),
    }
    fields.update(extra)
    return service.create_connection(fields)


def frozen(service, local):
    return service.freeze_for_run(local.workspace_id, local.user_id)


def test_schema_write_only_and_get_never_reads_or_returns_credentials(context):
    service, database, _local, secrets, _env, reads, _probes = context
    for provider in ("fred", "alpha_vantage", "typesafe_jev", "sec_edgar"):
        create(service, provider)
    public = json.dumps([row.model_dump(mode="json") for row in service.list_connections()])
    assert (
        "TEST-PRIVATE" not in public and "user@example.test" not in public and "_ref" not in public
    )
    assert reads == []
    with database.session() as session:
        rows = session.scalars(select(ResearchDataConnectionRecord)).all()
        assert all(
            str(value).startswith("research-data/")
            for row in rows
            for value in (row.credential_ref, row.user_agent_ref)
            if value
        )
    assert len(secrets._values) == 4
    properties = ResearchDataConnectionCreate.model_json_schema()["properties"]
    assert properties["api_key"]["anyOf"][0]["writeOnly"] is True
    assert properties["user_agent"]["anyOf"][0]["writeOnly"] is True


def test_configured_web_never_uses_environment_when_missing_disabled_or_cleared(
    context, monkeypatch
):
    service, _db, local, _secrets, env, reads, _probes = context
    monkeypatch.setenv("FRED_API_KEY", "CLI-ONLY")
    env["FRED_API_KEY"] = "ENV-IMPORT"
    with service.runtime_context_for_legacy():
        with pytest.raises(fred.FredNotConfiguredError):
            fred.get_api_key()
        assert post_screen.jev_screen("MSFT") is None
        with pytest.raises(ValueError):
            sec_edgar._user_agent()
    assert reads == [] and fred.get_api_key() == "CLI-ONLY"
    connection = create(service)
    with service.runtime_context_for_legacy():
        assert fred.get_api_key() == "TEST-PRIVATE"
    connection = service.clear_connection(connection.id, connection.revision)
    assert not connection.enabled and not connection.credential_configured
    with (
        data_credentials_scope(service.build_runtime_resolver(frozen(service, local))),
        pytest.raises(fred.FredNotConfiguredError),
    ):
        fred.get_api_key()
    assert reads == []


def test_explicit_environment_import_pins_key_and_public_jev_settings_in_keychain(context):
    service, _db, local, _secrets, env, reads, _probes = context
    env.update(
        TYPESAFE_API_KEY="IMPORTED-ONE",
        TYPESAFE_BASE_URL="https://openrouter.ai/api",
        TYPESAFE_DEFAULT_MODEL="vendor/jev-version",
    )
    connection = service.create_connection(
        {"provider": "typesafe_jev", "name": "Import", "credential_source": "legacy_environment"}
    )
    assert connection.credential_configured and connection.base_url == "https://openrouter.ai/api"
    assert connection.model == "vendor/jev-version"
    snapshot = frozen(service, local)
    reads.clear()
    env.update(
        TYPESAFE_API_KEY="ENV-CHANGED",
        TYPESAFE_BASE_URL="https://api.typesafe.ai",
        TYPESAFE_DEFAULT_MODEL="other-model",
    )
    settings = service.build_runtime_resolver(snapshot)("typesafe_jev")
    assert settings == {
        "api_key": "IMPORTED-ONE",
        "base_url": "https://openrouter.ai/api",
        "model": "vendor/jev-version",
    }
    assert reads == []  # Runtime is independent of later environment mutations.
    changed = service.update_connection(
        connection.id, {"revision": connection.revision, "credential_source": "legacy_environment"}
    )
    assert changed.base_url == "https://api.typesafe.ai" and changed.model == "other-model"
    with pytest.raises(ResearchDataConnectionError) as error:
        service.build_runtime_resolver(snapshot)
    assert error.value.code == "research_data_configuration_changed"


def test_keychain_to_environment_import_does_not_retain_previous_model_or_endpoint(context):
    service, _db, local, _secrets, env, _reads, _probes = context
    connection = create(
        service, "typesafe_jev", model="old-model", base_url="https://api.typesafe.ai"
    )
    env.update(
        TYPESAFE_API_KEY="IMPORTED",
        TYPESAFE_DEFAULT_MODEL="new-model",
        TYPESAFE_BASE_URL="https://openrouter.ai/api",
    )
    changed = service.update_connection(
        connection.id, {"revision": 1, "credential_source": "legacy_environment"}
    )
    assert changed.model == "new-model" and changed.base_url == "https://openrouter.ai/api"
    assert (
        service.build_runtime_resolver(frozen(service, local))("typesafe_jev")["api_key"]
        == "IMPORTED"
    )


def test_cas_rotation_disable_and_new_connection_invalidate_frozen_queued_account_set(context):
    service, _db, local, secrets, _env, _reads, _probes = context
    connection = create(service)
    snapshot = frozen(service, local)
    with service.database.session() as session:
        reference = session.get(ResearchDataConnectionRecord, connection.id).credential_ref
    rotated = service.update_connection(connection.id, {"revision": 1, "api_key": "ROTATED"})
    assert secrets.get(reference) is None
    with pytest.raises(ResearchDataConnectionError) as error:
        service.build_runtime_resolver(snapshot)
    assert error.value.code == "research_data_configuration_changed"
    with pytest.raises(ResearchDataConnectionError):
        service.update_connection(connection.id, {"revision": 1, "api_key": "STALE"})
    assert "STALE" not in secrets._values.values()
    resolver = service.build_runtime_resolver(frozen(service, local))
    service.update_connection(connection.id, {"revision": rotated.revision, "enabled": False})
    with pytest.raises(ResearchDataConnectionError):
        resolver("fred")


def test_provider_has_one_account_per_owner_and_other_owners_cannot_read_or_change_it(context):
    service, database, local, secrets, _env, _reads, _probes = context
    connection = create(service)
    with pytest.raises(ResearchDataConnectionError) as error:
        create(service)
    assert error.value.code == "research_provider_exists"
    with database.session() as session:
        session.add(User(id="friend", name="Friend"))
        session.flush()
        session.add(
            Membership(
                id="friend-member",
                user_id="friend",
                workspace_id=local.workspace_id,
                role="researcher",
            )
        )
    friend = ResearchDataConnectionService(
        database, replace(local, user_id="friend"), secret_store=secrets
    )
    assert friend.list_connections() == []
    with pytest.raises(ResearchDataConnectionError) as error:
        friend.update_connection(connection.id, {"revision": 1, "name": "change"})
    assert error.value.status_code == 403
    with pytest.raises(ResearchDataConnectionError):
        friend.freeze_for_run(local.workspace_id, local.user_id)


def test_secret_store_failure_does_not_create_file_or_database_fallback(context):
    service, database, _local, _secrets, _env, _reads, _probes = context

    class Broken(MemorySecretStore):
        def set(self, reference, value):
            raise SecretStoreError("do not echo")

    service.secrets = Broken()
    with pytest.raises(ResearchDataConnectionError) as error:
        create(service)
    assert error.value.code == "secret_store_unavailable" and "do not echo" not in str(error.value)
    with database.session() as session:
        assert session.scalar(select(ResearchDataConnectionRecord)) is None


def test_research_manifest_freezes_refs_without_keychain_refs_or_contact_values(context, tmp_path):
    accounts, database, local, secrets, _env, _reads, _probes = context
    connection = create(accounts)
    create(accounts, "sec_edgar")
    research = ResearchService(
        database, tmp_path / "artifacts", data_snapshot_provider=accounts.freeze_for_run
    )
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    run = research.create_run({"ticker": "NVDA", "date": "2026-01-01"}, **scope)
    manifest = research.get_manifest(run["id"], **scope)
    encoded = json.dumps(manifest)
    assert (
        "TEST-PRIVATE" not in encoded
        and "research-data/" not in encoded
        and "user@example.test" not in encoded
    )
    assert manifest["data_connections"]["providers"]["fred"]["connection_id"] == connection.id
    runtime = create_runtime_resolver(database, {}, secret_store=secrets)(manifest)
    private = runtime.pop("__data_connection_resolver")
    assert "TEST-PRIVATE" not in json.dumps(runtime)
    with data_credentials_scope(private):
        assert fred.get_api_key() == "TEST-PRIVATE"
        assert sec_edgar._user_agent() == "Research User user@example.test"


def test_actual_fred_alpha_requests_consume_scoped_keys_not_environment(context, monkeypatch):
    accounts, _db, _local, _secrets, _env, _reads, _probes = context
    create(accounts)
    create(accounts, "alpha_vantage", api_key="ALPHA-SCOPED")
    captured = []

    class Response:
        status_code = 200
        text = '{"Symbol":"MSFT"}'

        def json(self):
            return {"seriess": [{"id": "FEDFUNDS"}]}

    def request(url, **kwargs):
        captured.append((url, kwargs))
        return Response()

    monkeypatch.setattr(fred, "get_scrubbed", request)
    monkeypatch.setattr(alpha, "get_scrubbed", request)
    monkeypatch.setenv("FRED_API_KEY", "WRONG-ENV")
    with accounts.runtime_context_for_legacy():
        fred._request("series", {"series_id": "FEDFUNDS"})
        alpha._make_api_request("OVERVIEW", {"symbol": "MSFT"})
    assert captured[0][1]["params"]["api_key"] == "TEST-PRIVATE"
    assert captured[1][1]["params"]["apikey"] == "ALPHA-SCOPED"


@pytest.mark.parametrize("phase", ["queued", "running"])
def test_account_changes_fail_the_entire_worker_even_if_a_tool_swallows_the_error(
    context, tmp_path, phase
):
    accounts, database, local, secrets, _env, _reads, _probes = context
    connection = create(accounts)
    research = ResearchService(
        database, tmp_path / "artifacts", data_snapshot_provider=accounts.freeze_for_run
    )
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    run = research.create_run({"ticker": "NVDA", "date": "2026-01-01"}, **scope)
    constructed = []

    class ChangingGraph:
        def __init__(self, analysts, config, callbacks):
            constructed.append(config)
            assert "TEST-PRIVATE" not in json.dumps(config)
            assert not any(key.startswith("__data") for key in config)
            self.propagator = SimpleNamespace(get_graph_args=lambda **kwargs: {})

        def create_run_state(self, *args):
            return {}

        def begin_checkpoint(self, *args):
            return None

        def checkpoint_input(self, state):
            return state

        def stream_run(self, *args, **kwargs):
            assert fred.get_api_key() == "TEST-PRIVATE"
            accounts.update_connection(connection.id, {"revision": 1, "enabled": False})
            # ToolNode/vendor fallback may swallow a tool exception. The worker must still fail.
            with suppress(ResearchDataConnectionError):
                fred.get_api_key()
            yield [], {"final_trade_decision": "Final Rating: Buy"}

    if phase == "queued":
        accounts.update_connection(connection.id, {"revision": 1, "api_key": "ROTATED"})
    worker = ResearchWorker(
        research,
        create_runtime_resolver(database, {}, secret_store=secrets),
        graph_factory=ChangingGraph,
    )
    assert worker.run_once()
    result = research.get_run(run["id"], **scope)
    assert result["status"] == "failed" and "配置已变更" in result["error"]
    with database.session() as session:
        assert (
            session.get(ResearchRun, run["id"]).error_code == "research_data_configuration_changed"
        )
    assert len(constructed) == (1 if phase == "running" else 0)
    assert "TEST-PRIVATE" not in json.dumps(result)


def test_contexts_and_nested_tasks_are_isolated_without_mutating_environment(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "CLI-UNCHANGED")

    def work(key):
        with data_credentials_scope(lambda provider: {"api_key": key}):
            own = fred.get_api_key()
            with (
                data_credentials_scope(lambda provider: {}),
                pytest.raises(fred.FredNotConfiguredError),
            ):
                fred.get_api_key()
            return own, fred.get_api_key()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(work, ["A", "B"])) == [("A", "A"), ("B", "B")]
    assert configured_credentials("fred") is None and fred.get_api_key() == "CLI-UNCHANGED"


def test_jev_threads_use_explicit_frozen_settings(context, monkeypatch):
    accounts, _db, _local, _secrets, _env, _reads, _probes = context
    create(accounts, "typesafe_jev", model="versioned-jev", base_url="https://openrouter.ai/api")
    captured = []

    class Response:
        status_code = 200

        def json(self):
            return {
                "answers": {
                    "about": {"type": "noul", "noul": 0.9},
                    "stance": {"type": "choice", "choice": "bullish", "confidence": 0.9},
                }
            }

    def post(url, **kwargs):
        captured.append((url, kwargs))
        return Response()

    monkeypatch.setattr(post_screen.requests, "post", post)
    monkeypatch.setattr(post_screen, "resolve_instrument_identity", lambda ticker: {})
    monkeypatch.setenv("TYPESAFE_API_KEY", "WRONG-ENV")
    with accounts.runtime_context_for_legacy():
        screen = post_screen.jev_screen("MSFT")
        keep, _note = screen(["one", "two"])
    assert keep == [True, True] and len(captured) == 2
    for url, kwargs in captured:
        assert url == "https://openrouter.ai/api/v1/systemone"
        assert kwargs["headers"]["Authorization"] == "Bearer TEST-PRIVATE"
        assert kwargs["json"]["model"] == "versioned-jev" and kwargs["allow_redirects"] is False


def test_fred_400_and_url_encoded_http_errors_never_echo_secret_or_attached_objects(
    monkeypatch, caplog
):
    key = "private+/percent%value"

    class Bad:
        status_code = 400
        text = key

        def json(self):
            return {"error_message": key, "nested": {"apikey": key}}

    monkeypatch.setattr(fred, "get_scrubbed", lambda *args, **kwargs: Bad())
    with (
        data_credentials_scope(lambda provider: {"api_key": key}),
        pytest.raises(ValueError) as error,
    ):
        fred._request("series", {})
    assert key not in str(error.value)
    encoded = quote(key, safe="")

    def fail(*args, **kwargs):
        raise requests.HTTPError(
            "https://host.test/path?api_key=" + encoded,
            request=requests.Request("GET", "https://host.test/?api_key=" + encoded).prepare(),
        )

    monkeypatch.setattr(net.requests, "get", fail)
    with pytest.raises(requests.HTTPError) as error:
        net.get_scrubbed("https://host.test", params={}, timeout=1, secret=key)
    assert (
        encoded not in str(error.value)
        and error.value.request is None
        and error.value.__context__ is None
    )
    with caplog.at_level(logging.DEBUG, logger="urllib3.connectionpool"):
        logging.getLogger("urllib3.connectionpool").debug(
            "GET /path?api_key=%s&series=foo", encoded
        )
    assert encoded not in caplog.text and "api_key=***" in caplog.text


@pytest.mark.parametrize("field", ["Information", "Note", "Error Message"])
def test_alpha_200_error_bodies_do_not_echo_raw_or_encoded_authentication(field, monkeypatch):
    key = "private+/percent%value"
    encoded = quote(key, safe="")

    class Response:
        text = json.dumps({field: "API key invalid " + encoded})

    monkeypatch.setattr(alpha, "get_scrubbed", lambda *args, **kwargs: Response())
    with (
        data_credentials_scope(lambda provider: {"api_key": key}),
        pytest.raises(VendorError) as error,
    ):
        alpha._make_api_request("OVERVIEW", {"symbol": "MSFT"})
    assert key not in str(error.value) and encoded not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.typesafe.ai",
        "https://evil.test",
        "https://user:pass@api.typesafe.ai",
        "https://api.typesafe.ai?token=hidden",
        "https://openrouter.ai/api/other",
    ],
)
def test_jev_endpoints_are_public_fixed_official_profiles(url):
    with pytest.raises(ValidationError):
        ResearchDataConnectionCreate(
            provider="typesafe_jev", name="Jev", api_key="fixture", base_url=url
        )


def test_test_action_is_fresh_cas_and_never_marks_saved_keys_as_verified(context):
    accounts, _db, _local, _secrets, _env, _reads, probes = context
    connection = create(accounts)
    assert connection.test_status == "untested"
    tested = accounts.test_connection(connection.id, 1)
    assert (
        tested.success
        and tested.connection.revision == 2
        and tested.connection.test_status == "passed"
    )
    assert probes[0][0] == "fred"
    with pytest.raises(ResearchDataConnectionError):
        accounts.test_connection(connection.id, 1)
    accounts.tester = lambda provider, settings: (_ for _ in ()).throw(RuntimeError("TEST-PRIVATE"))
    tested = accounts.test_connection(connection.id, 2)
    assert not tested.success and "TEST-PRIVATE" not in tested.message


def test_router_and_clear_tombstone_have_no_secret_fields(context):
    accounts, _db, _local, _secrets, _env, _reads, _probes = context
    app = FastAPI()
    app.include_router(create_research_data_router(lambda: accounts))
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/research-data-connections",
            json={"provider": "fred", "name": "FRED", "api_key": "TEST-PRIVATE"},
        )
        assert created.status_code == 201 and "TEST-PRIVATE" not in created.text
        identifier = created.json()["id"]
        response = client.post(
            f"/api/v1/research-data-connections/{identifier}/clear", json={"revision": 1}
        )
        assert response.status_code == 200 and response.json()["enabled"] is False
        rows = client.get("/api/v1/research-data-connections").json()["connections"]
        assert len(rows) == 1 and "api_key" not in rows[0] and "credential_ref" not in rows[0]
