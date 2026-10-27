"""Custom model protocols keep transport explicit and authentication out of public data."""

import json
from unittest.mock import Mock

import httpx
import httpx2
import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.web.api import create_api_router
from tradingagents.web.api.model_schemas import ConnectionCreate, ModelConfigurationError
from tradingagents.web.application.model_transport import client_arguments
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.domain.model_configuration import resolve_protocol, validate_parameters
from tradingagents.web.infrastructure.database import (
    Database,
    initialize_database,
    migration_config,
)
from tradingagents.web.infrastructure.models import ModelConnection
from tradingagents.web.infrastructure.secrets import MemorySecretStore


@pytest.fixture
def service(tmp_path):
    database = initialize_database(tmp_path / "models.sqlite3")
    secrets = MemorySecretStore()
    current = ModelConfigurationService(
        database,
        database.local_workspace(),
        secret_store=secrets,
        tester=lambda snapshot, key: {"chat": True, "tools": "supported"},
    )
    yield current, database, secrets
    database.dispose()


def custom(service, **overrides):
    return service.create_connection(
        {
            "name": "Research gateway",
            "provider": "custom",
            "vendor_name": "My vendor",
            "api_protocol": "openai_chat_completions",
            "endpoint": "https://gateway.invalid/v1",
            "api_key": "private-key",
            **overrides,
        }
    )


def freeze(service, connection):
    item = service.create_profile(
        {
            "connection_id": connection["id"],
            "model_id": "organization/custom-model:version",
            "parameters": {"extra_body": {"thinking": {"type": "enabled"}}}
            if connection["resolved_protocol"].startswith("openai")
            else {},
        }
    )
    service.test_profile(item["id"], item["revision"])
    service.save_settings({"default_profile_id": item["id"]})
    return service.resolve_model_snapshot()


def test_custom_headers_are_secret_references_and_survive_rotation(service):
    current, database, secrets = service
    connection = custom(current, extra_headers={"X-Gateway-Token": "private-header-value"})
    assert connection["vendor_name"] == "My vendor"
    assert connection["headers_configured"] and connection["header_names"] == ["X-Gateway-Token"]
    frozen = freeze(current, connection)
    old = frozen["roles"]["market"]["extra_header_ref"]
    assert "private-header-value" not in str(frozen) + str(connection)
    assert "private-header-value" not in database.path.read_bytes().decode(errors="ignore")
    changed = current.update_connection(
        connection["id"],
        {
            "revision": connection["revision"],
            "extra_headers": {"X-Gateway-Token": "rotated-header-value"},
        },
    )
    assert changed["headers_configured"]
    assert secrets.get(old) == '{"X-Gateway-Token": "private-header-value"}'
    assert current.list_profiles()[0]["test_status"] == "untested"
    with pytest.raises(ModelConfigurationError):
        current.resolve_model_snapshot()


def test_header_only_and_explicit_unauthenticated_remote_connections(service):
    current, _, _ = service
    header_only = custom(
        current,
        auth_type="headers",
        api_key=None,
        extra_headers={"Authorization": "Bearer gateway-value"},
    )
    assert not header_only["credential_configured"] and header_only["headers_configured"]
    snapshot = freeze(current, header_only)["roles"]["market"]
    provider, parameters = client_arguments(
        snapshot, None, {"Authorization": "Bearer gateway-value"}
    )
    assert (
        provider == "openai_compatible"
        and parameters["default_headers"]["Authorization"] == "Bearer gateway-value"
    )
    remote = custom(current, auth_type="none", api_key=None)
    assert remote["endpoint"] == "https://gateway.invalid/v1"
    assert not remote["credential_configured"] and not remote["headers_configured"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "host.invalid"},
        {"Cookie": "private"},
        {"X-Test": "value\r\nAuthorization: private"},
        {"X-Test": "first", "x-test": "second"},
        {"Bad Header": "private"},
    ],
)
def test_invalid_headers_rejected_without_credential_echo(headers):
    with pytest.raises(ValidationError):
        ConnectionCreate(
            name="Bad", provider="custom", endpoint="https://gateway.invalid", extra_headers=headers
        )


def test_protocol_change_invalidates_probe_and_preserves_old_snapshot(service):
    current, _, _ = service
    connection = custom(current)
    frozen = freeze(current, connection)
    changed = current.update_connection(
        connection["id"], {"revision": connection["revision"], "api_protocol": "openai_responses"}
    )
    assert changed["resolved_protocol"] == "openai_responses"
    assert frozen["roles"]["market"]["api_protocol"] == "openai_chat_completions"
    assert current.list_profiles()[0]["test_status"] == "untested"
    current.update_connection(changed["id"], {"revision": changed["revision"], "auth_type": "none"})
    assert not current.list_connections()[0]["credential_configured"]


@pytest.mark.parametrize(
    "protocol,path",
    [
        ("openai_chat_completions", "/v1/chat/completions"),
        ("openai_responses", "/v1/responses"),
        ("anthropic_messages", "/v1/messages"),
    ],
)
def test_wire_protocol_model_and_headers_reach_real_sdk_with_mock_http(
    service, monkeypatch, protocol, path
):
    current, _, _ = service
    endpoint = (
        "https://gateway.invalid"
        if protocol == "anthropic_messages"
        else "https://gateway.invalid/v1"
    )
    connection = custom(
        current,
        endpoint=endpoint,
        api_protocol=protocol,
        extra_headers={"X-Gateway-Token": "private-token"},
    )
    snapshot = freeze(current, connection)
    requests = []
    wire = httpx2 if protocol == "anthropic_messages" else httpx

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == path
        assert request.headers["X-Gateway-Token"] == "private-token"
        assert payload["model"] == "organization/custom-model:version"
        if protocol == "openai_chat_completions":
            result = {
                "id": "chat-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "model": payload["model"],
                "object": "chat.completion",
                "created": 0,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        elif protocol == "openai_responses":
            result = {
                "id": "resp_test",
                "created_at": 0,
                "object": "response",
                "status": "completed",
                "model": payload["model"],
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        else:
            result = {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return wire.Response(200, json=result)

    with wire.Client(transport=wire.MockTransport(handle)) as client:

        def factory(provider, model, **kwargs):
            return create_llm_client(provider, model, http_client=client, **kwargs)

        monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
        llm = current.build_role_model_resolver(snapshot)("market")
        assert "private-token" not in json.dumps(llm.to_json(), default=str)
        assert llm.invoke("Reply OK").content == "OK"
    assert len(requests) == 1


def test_google_native_adapter_and_legacy_snapshots_use_explicit_credentials(service, monkeypatch):
    current, _, _ = service
    connection = custom(
        current, api_protocol="google_genai", endpoint="https://google-gateway.invalid"
    )
    snapshot = freeze(current, connection)
    captured = []

    def factory(provider, model, **kwargs):
        captured.append((provider, model, kwargs))
        return Mock(get_llm=lambda: "native-client")

    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    assert current.build_role_model_resolver(snapshot)("market") == "native-client"
    assert captured[0][0] == "google" and captured[0][2]["api_key"] == "private-key"
    assert resolve_protocol("openai", "https://api.openai.com/v1") == "openai_responses"
    assert resolve_protocol("openai", "https://proxy.invalid/v1") == "openai_chat_completions"
    assert resolve_protocol("anthropic", None) == "anthropic_messages"


def test_google_custom_headers_are_redacted_from_langchain_serialization():
    llm = create_llm_client(
        "google",
        "gemini-custom",
        api_key="fake-key",
        additional_headers={"X-Key": "PRIVATE-HEADER"},
    ).get_llm()
    assert "PRIVATE-HEADER" not in json.dumps(llm.to_json(), default=str)


@pytest.mark.parametrize(
    "parameters",
    [
        {"extra_body": {"api_key": "private"}},
        {"extra_body": {"model": "other"}},
        {"extra_body": {"thinking": {"access_token": "private"}}},
        {"extra_body": {"value": float("nan")}},
        {"extra_body": {"value": "x" * 4001}},
        {"top_p": 1.1},
        {"seed": 1.2},
        {"stop": [""]},
    ],
)
def test_custom_parameters_are_bounded_and_cannot_override_credentials_or_inputs(parameters):
    with pytest.raises(ModelConfigurationError):
        validate_parameters(parameters, "custom", "openai_chat_completions")


def test_custom_model_http_contract_never_returns_header_values(service):
    current, _, _ = service
    app = FastAPI()
    app.include_router(create_api_router(model_service_reader=lambda: current))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/model-connections",
            json={
                "name": "My gateway",
                "provider": "custom",
                "vendor_name": "Independent vendor",
                "api_protocol": "openai_responses",
                "auth_type": "headers",
                "endpoint": "https://proxy.invalid/v1",
                "extra_headers": {"Authorization": "Bearer PRIVATE-VALUE"},
            },
        )
        assert response.status_code == 201
        assert response.json()["resolved_protocol"] == "openai_responses"
        assert response.json()["header_names"] == ["Authorization"]
        assert "PRIVATE-VALUE" not in response.text + client.get("/api/v1/model-connections").text


def test_migration_from_existing_connections_preserves_auto_protocol(tmp_path):
    database = Database(tmp_path / "old.sqlite3")
    database.path.parent.mkdir(parents=True, exist_ok=True)
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "0002_data_settings")
        connection.exec_driver_sql(
            "INSERT INTO users (id,name,created_at) VALUES ('u','test',CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO workspaces (id,owner_id,name,created_at) VALUES ('w','u','test',CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO model_connections (id,workspace_id,owner_id,name,provider,auth_type,scope,enabled,revision,created_at,updated_at) VALUES ('c','w','u','Existing','openai','api_key','private',1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    database.migrate()
    with database.session() as session:
        old = session.scalar(select(ModelConnection).where(ModelConnection.id == "c"))
        assert old.name == "Existing" and old.api_protocol == "auto" and old.header_names == []
    database.dispose()
