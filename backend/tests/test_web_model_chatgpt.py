"""Existing local OAuth becomes an explicit model connection without exporting tokens."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from tradingagents.llm_clients import chatgpt_auth
from tradingagents.web.api.model_schemas import ConnectionCreate, ModelConfigurationError
from tradingagents.web.application.model_transport import client_arguments
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.domain.model_configuration import resolve_protocol, validate_parameters
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import ModelConnection
from tradingagents.web.infrastructure.secrets import MemorySecretStore


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "models.sqlite3")
    state = {"status": "not_authorized"}
    tester = Mock(return_value={"chat": True, "tools": "supported"})
    secrets = MemorySecretStore()
    service = ModelConfigurationService(
        database,
        database.local_workspace(),
        secret_store=secrets,
        tester=tester,
        local_authorization_reader=lambda: deepcopy(state),
    )
    yield service, database, state, tester, secrets
    database.dispose()


def connection(service, **overrides):
    return service.create_connection(
        ConnectionCreate(
            name="My local subscription", provider="chatgpt", auth_type="oauth_local", **overrides
        ).model_dump()
    )


def profile(service, item):
    return service.create_profile({"connection_id": item["id"], "model_id": "manual-plan-model"})


def authorize(state, identity="a" * 64):
    state.update(status="authorized", identity_fingerprint=identity)


def test_save_without_login_reports_missing_authorization_and_does_not_probe(context):
    service, database, _state, tester, secrets = context
    provider = next(row for row in service.providers() if row["id"] == "chatgpt")
    assert provider["auth_types"] == ["oauth_local"]
    assert provider["local_authorization"]["status"] == "not_authorized"
    item = connection(service)
    model = profile(service, item)
    assert item["auth_type"] == "oauth_local"
    assert item["resolved_protocol"] == "openai_responses"
    assert not item["credential_configured"] and not item["headers_configured"]
    assert model["test_status"] == "untested"
    with pytest.raises(ModelConfigurationError) as failure:
        service.test_profile(model["id"], model["revision"])
    assert failure.value.code == "local_authorization_required"
    tester.assert_not_called()
    with database.session() as session:
        stored = session.scalar(select(ModelConnection).where(ModelConnection.id == item["id"]))
        assert stored.credential_ref is None and stored.extra_header_ref is None
    assert secrets.get("unused") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "fake-credential"},
        {"extra_headers": {"Authorization": "fake-credential"}},
        {"endpoint": "https://gateway.invalid/v1"},
        {"scope": "workspace"},
        {"api_protocol": "openai_chat_completions"},
    ],
)
def test_local_subscription_rejects_user_credentials_relays_sharing_and_other_protocols(
    context, overrides
):
    service, _database, _state, _tester, _secrets = context
    with pytest.raises(ModelConfigurationError):
        connection(service, **overrides)
    assert service.list_connections() == []


def test_updates_cannot_turn_a_local_subscription_into_a_key_or_relay(context):
    service, _database, _state, _tester, _secrets = context
    item = connection(service)
    for changes in (
        {"auth_type": "api_key"},
        {"api_key": "fake-credential"},
        {"scope": "workspace"},
        {"endpoint": "https://relay.invalid/v1"},
        {"extra_headers": {"X-Token": "fake-credential"}},
    ):
        with pytest.raises(ModelConfigurationError):
            service.update_connection(item["id"], {"revision": 1, **changes})
    assert service.list_connections()[0]["revision"] == 1


def test_authorized_local_connection_still_needs_testing_and_can_bind_roles(context, monkeypatch):
    service, database, state, tester, _secrets = context
    authorize(state)
    state.update(
        email="private@example.invalid", access_token="fake-access", subject="private-subject"
    )
    item = connection(service)
    model = profile(service, item)
    service.save_settings({"default_profile_id": model["id"]})
    with pytest.raises(ModelConfigurationError) as failure:
        service.resolve_model_snapshot()
    assert failure.value.code == "model_not_tested"
    tested = service.test_profile(model["id"], model["revision"])
    assert tested["success"] and tester.call_count == 1
    snapshot = service.resolve_model_snapshot()
    public = json.dumps([service.providers(), service.list_connections(), snapshot], default=str)
    assert all(
        value not in public
        for value in ("private@example.invalid", "fake-access", "private-subject")
    )
    assert snapshot["roles"]["market"]["provider"] == "chatgpt"
    assert snapshot["roles"]["market"]["credential_ref"] is None
    assert snapshot["roles"]["market"]["local_authorization_fingerprint"] == "a" * 64
    assert b"fake-access" not in database.path.read_bytes()
    client = Mock()
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", client)
    resolver = service.build_role_model_resolver(snapshot)
    resolver("market")
    assert client.call_args.args[0] == "chatgpt"
    assert "api_key" not in client.call_args.kwargs
    assert "default_headers" not in client.call_args.kwargs
    authorize(state, "b" * 64)
    with pytest.raises(ModelConfigurationError) as failure:
        resolver("market")
    assert failure.value.code == "local_authorization_changed"
    with pytest.raises(ModelConfigurationError) as failure:
        service.resolve_model_snapshot()
    assert failure.value.code == "local_authorization_changed"
    changed = service.test_profile(model["id"], tested["profile"]["revision"])
    assert changed["success"]
    assert service.resolve_model_snapshot()["signature"] != snapshot["signature"]


@pytest.mark.parametrize(
    "parameter", ["temperature", "top_p", "max_tokens", "seed", "stop", "extra_body"]
)
def test_subscription_rejects_parameters_which_the_plan_does_not_accept(parameter):
    with pytest.raises(ModelConfigurationError):
        validate_parameters({parameter: 1}, "chatgpt", "openai_responses")
    assert validate_parameters(
        {"timeout": 30, "max_retries": 0, "reasoning_effort": "low"}, "chatgpt", "openai_responses"
    )
    assert resolve_protocol("chatgpt", "https://api.openai.com/v1") == "openai_responses"


def test_transport_never_uses_an_api_key_instead_of_oauth():
    snapshot = {
        "provider": "chatgpt",
        "auth_type": "oauth_local",
        "base_url": "https://api.openai.com/v1",
        "local_authorization_fingerprint": "a" * 64,
    }
    provider, arguments = client_arguments(snapshot, None, {})
    assert provider == "chatgpt" and "api_key" not in arguments
    with pytest.raises(ModelConfigurationError):
        client_arguments(snapshot, "fake-credential", {})
    with pytest.raises(ModelConfigurationError):
        client_arguments({**snapshot, "base_url": "https://relay.invalid"}, None, {})


def auth_record():
    return {
        "host_id": "fake-host",
        "account": {
            "client_id": "fake-client",
            "subject": "fake-subject",
            "email": "private@example.invalid",
        },
        "tokens": {
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "expires_at": 2000,
            "scopes": [chatgpt_auth.PLAN_SCOPE],
        },
    }


def test_local_status_checks_scope_and_expiry_without_network_or_raw_identity(monkeypatch):
    data = auth_record()
    monkeypatch.setattr(chatgpt_auth, "_read", lambda: deepcopy(data))
    monkeypatch.setattr(chatgpt_auth.time, "time", lambda: 1000)
    requests = Mock(side_effect=AssertionError("status cannot contact OAuth or model API"))
    monkeypatch.setattr(chatgpt_auth.requests, "get", requests)
    monkeypatch.setattr(chatgpt_auth.requests, "post", requests)
    status = chatgpt_auth.authorization_status()
    assert status["status"] == "authorized"
    assert set(status) == {"status", "identity_fingerprint"}
    data["tokens"]["access_token"] = "rotated-fake-access"
    assert chatgpt_auth.authorization_status() == status
    data["tokens"]["scopes"] = ["openid"]
    assert chatgpt_auth.authorization_status() == {"status": "not_authorized"}
    data["tokens"]["scopes"] = [chatgpt_auth.PLAN_SCOPE]
    data["tokens"]["expires_at"] = 500
    assert chatgpt_auth.authorization_status()["status"] == "authorized"
    data["tokens"].pop("refresh_token")
    assert chatgpt_auth.authorization_status() == {"status": "not_authorized"}
    requests.assert_not_called()


def test_guarded_oauth_callable_rejects_account_switch_before_fetching_a_token(monkeypatch):
    data = auth_record()
    monkeypatch.setattr(chatgpt_auth, "_read", lambda: deepcopy(data))
    monkeypatch.setattr(chatgpt_auth.time, "time", lambda: 1000)
    token = Mock(return_value="fake-access")
    monkeypatch.setattr(chatgpt_auth, "access_token", token)
    identity = chatgpt_auth.authorization_status()["identity_fingerprint"]
    guarded = chatgpt_auth.credential(identity)
    assert guarded() == "fake-access"
    data["account"]["subject"] = "another-fake-subject"
    with pytest.raises(chatgpt_auth.ChatGPTAuthError):
        guarded()
    assert token.call_count == 1


def test_installed_chatgpt_client_keeps_its_oauth_callable_and_responses_transport(monkeypatch):
    from tradingagents.llm_clients.factory import create_llm_client
    from tradingagents.llm_clients.openai_client import ChatGPTPlanChatOpenAI

    credential = Mock(return_value=lambda: "fake-access")
    monkeypatch.setattr(chatgpt_auth, "credential", credential)
    model = create_llm_client(
        "chatgpt",
        "manual-plan-model",
        base_url="https://api.openai.com/v1",
        chatgpt_authorization_fingerprint="a" * 64,
    ).get_llm()
    assert isinstance(model, ChatGPTPlanChatOpenAI)
    assert model.use_responses_api and callable(model.openai_api_key)
    assert model.store is False
    credential.assert_called_once_with("a" * 64)
    assert "chatgpt_authorization_fingerprint" not in model.model_kwargs
