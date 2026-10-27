"""Password accounts/session boundaries using isolated SQLite and fake test identities."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tradingagents.web.api.auth_router import create_auth_router
from tradingagents.web.api.auth_schemas import (
    AuthLoginRequest,
    AuthPasswordChange,
    AuthRegisterRequest,
    AuthRegistrationSettings,
    AuthSetupRequest,
)
from tradingagents.web.application.authentication import (
    SESSION_COOKIE,
    AuthenticationService,
    AuthError,
)
from tradingagents.web.infrastructure.auth_models import (
    AccountRecord,
    AccountSessionRecord,
    AuthenticationRateLimitRecord,
)
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, ResearchRun, User, Watchlist

PASSWORD = "test-only passphrase 123"


@pytest.fixture
def auth(tmp_path):
    database = initialize_database(tmp_path / "accounts.sqlite3")
    clock = [datetime(2026, 10, 27, 12, tzinfo=UTC)]
    service = AuthenticationService(database, clock=lambda: clock[0], password_hasher=PasswordHasher(
        time_cost=1, memory_cost=8192, parallelism=1))
    token_path = service.ensure_setup_token()
    yield database, service, clock, token_path
    database.dispose()


def setup(service, token_path, username="owner"):
    return service.setup_owner(AuthSetupRequest(username=username, password=PASSWORD,
        setup_token=token_path.read_text().strip(), display_name="Owner"))


def register(service, username="friend"):
    return service.register(AuthRegisterRequest(username=username, password=PASSWORD))


def test_setup_adopts_legacy_identity_only_with_private_single_use_token(auth):
    database, service, _, token_path = auth
    local = database.local_workspace()
    assert service.ensure_setup_token() == token_path
    assert token_path.stat().st_mode & 0o777 == 0o600
    status = service.session_status()
    assert status.setup_required and not status.authenticated and status.csrf_token is None
    assert token_path.read_text().strip() not in status.model_dump_json()
    with pytest.raises(AuthError, match="本机所有者"):
        register(service)
    with pytest.raises(AuthError, match="令牌无效"):
        service.setup_owner(AuthSetupRequest(username="intruder", password=PASSWORD, setup_token="wrong" * 10))
    result = setup(service, token_path)
    identity = service.authenticate(result.token)
    assert identity.workspace.workspace_id == local.workspace_id
    assert identity.user_id == local.user_id and identity.is_admin and identity.can_use_legacy_credentials
    assert not token_path.exists() and service.ensure_setup_token() is None
    with pytest.raises(AuthError, match="已初始化"):
        service.setup_owner(AuthSetupRequest(username="otherowner", password=PASSWORD, setup_token="a" * 43))
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AccountRecord)) == 1
        account = session.get(AccountRecord, identity.user_id)
        assert account.password_hash.startswith("$argon2id$") and PASSWORD not in account.password_hash


def test_open_registration_creates_independent_empty_workspaces_and_no_legacy_permission(auth):
    database, service, _, token_path = auth
    owner = setup(service, token_path)
    friend = register(service)
    second = register(service, "friend_two")
    owner_scope, friend_scope = owner.status.user.workspace, friend.status.user.workspace
    assert owner_scope.id != friend_scope.id != second.status.user.workspace.id
    friend_identity = service.authenticate(friend.token)
    assert not friend_identity.is_admin and not friend_identity.can_use_legacy_credentials
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AccountRecord)) == 3
        assert session.scalar(select(func.count()).select_from(Membership)) == 3
        assert session.scalar(select(func.count()).select_from(ResearchRun).where(ResearchRun.owner_id == friend.status.user.id)) == 0
        lists = session.scalars(select(Watchlist).where(Watchlist.owner_id == friend.status.user.id)).all()
        assert len(lists) == 1 and lists[0].name == "我的自选"
    with pytest.raises(AuthError, match="用户名不可用"):
        register(service, "FRIEND")


def test_owner_token_rotation_and_expiry_never_allow_old_token_to_claim_data(auth):
    _, service, clock, token_path = auth
    original = token_path.read_text().strip()
    service.ensure_setup_token(rotate=True)
    replacement = token_path.read_text().strip()
    assert replacement != original
    with pytest.raises(AuthError, match="令牌无效"):
        service.setup_owner(AuthSetupRequest(username="owner", password=PASSWORD, setup_token=original))
    clock[0] += timedelta(days=1)
    with pytest.raises(AuthError, match="令牌无效"):
        service.setup_owner(AuthSetupRequest(username="owner", password=PASSWORD, setup_token=replacement))
    assert service.ensure_setup_token() == token_path
    assert token_path.read_text().strip() != replacement
    assert setup(service, token_path).status.authenticated


def test_sessions_only_store_digests_and_rotation_revokes_previous_browser_cookie(auth):
    database, service, _, token_path = auth
    result = setup(service, token_path)
    first = result.token
    again = service.login(AuthLoginRequest(username="OWNER", password=PASSWORD), replaced_token=first)
    assert again.token != first and again.status.csrf_token != result.status.csrf_token
    with pytest.raises(AuthError, match="登录已过期"):
        service.authenticate(first)
    with database.session() as session:
        rows = session.scalars(select(AccountSessionRecord)).all()
        assert len(rows) == 1
        assert rows[0].token_digest == hashlib.sha256(again.token.encode()).hexdigest()
        assert again.token not in repr(rows[0].__dict__)
    assert service.logout(again.token).authenticated is False
    with pytest.raises(AuthError):
        service.authenticate(again.token)


def test_session_poll_does_not_extend_idle_expiry_and_real_activity_touch_is_throttled(auth):
    database, service, clock, token_path = auth
    result = setup(service, token_path)
    initial = clock[0]
    clock[0] += timedelta(seconds=30)
    service.authenticate(result.token)
    with database.session() as session:
        assert session.scalar(select(AccountSessionRecord.last_seen_at)) == initial
    clock[0] += timedelta(seconds=31)
    service.authenticate(result.token)
    with database.session() as session:
        assert session.scalar(select(AccountSessionRecord.last_seen_at)) == clock[0]
    clock[0] += timedelta(hours=11, minutes=59)
    assert service.session_status(result.token).authenticated
    clock[0] += timedelta(minutes=2)
    assert not service.session_status(result.token).authenticated


def test_absolute_expiry_survives_activity_and_disabled_account_loses_all_sessions(auth):
    database, service, clock, token_path = auth
    result = setup(service, token_path)
    for _ in range(7 * 3 - 1):
        clock[0] += timedelta(hours=8)
        service.authenticate(result.token)
    clock[0] += timedelta(hours=8)
    with pytest.raises(AuthError, match="登录已过期"):
        service.authenticate(result.token)
    second = service.login(AuthLoginRequest(username="owner", password=PASSWORD))
    with database.session() as session:
        session.get(AccountRecord, result.status.user.id).disabled = True
    with pytest.raises(AuthError):
        service.authenticate(second.token)


def test_csrf_bound_to_cookie_and_password_change_invalidates_other_sessions(auth):
    _, service, _, token_path = auth
    first = setup(service, token_path)
    other = service.login(AuthLoginRequest(username="owner", password=PASSWORD))
    service.validate_csrf(first.token, first.status.csrf_token)
    with pytest.raises(AuthError, match="页面验证"):
        service.validate_csrf(first.token, other.status.csrf_token)
    with pytest.raises(AuthError, match="当前密码"):
        service.change_password(first.token, AuthPasswordChange(current_password="wrong", new_password="new test password 456"))
    changed = service.change_password(first.token, AuthPasswordChange(current_password=PASSWORD,
        new_password="new test password 456"))
    assert changed.status.authenticated
    for value in (first.token, other.token):
        with pytest.raises(AuthError):
            service.authenticate(value)
    with pytest.raises(AuthError):
        service.login(AuthLoginRequest(username="owner", password=PASSWORD))
    assert service.login(AuthLoginRequest(username="owner", password="new test password 456")).status.authenticated


def test_unknown_disabled_and_wrong_password_use_same_public_error_and_limits_persist(auth):
    database, service, _, token_path = auth
    setup(service, token_path)
    errors = []
    for username in ("unknown", "owner"):
        with pytest.raises(AuthError) as error:
            service.login(AuthLoginRequest(username=username, password="wrong"))
        errors.append((error.value.code, error.value.message, error.value.status_code))
    assert errors[0] == errors[1] == ("invalid_credentials", "用户名或密码不正确。", 401)
    for _ in range(11):
        with pytest.raises(AuthError):
            service.login(AuthLoginRequest(username="owner", password="wrong"))
    with pytest.raises(AuthError) as limited:
        service.login(AuthLoginRequest(username="owner", password=PASSWORD))
    assert limited.value.status_code == 429
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AuthenticationRateLimitRecord)) > 0


def test_concurrent_same_username_registration_and_owner_claim_are_atomic(auth):
    database, service, _, token_path = auth
    setup(service, token_path)
    def create(_):
        try:
            return register(service, "same_username").status.user.id
        except AuthError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert results.count("username_unavailable") == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AccountRecord)) == 2
        assert session.scalar(select(func.count()).select_from(User)) == 2


def test_concurrent_first_owner_setup_can_only_bind_one_account(auth):
    database, service, _, token_path = auth
    secret = token_path.read_text().strip()
    def claim(index):
        try:
            result = service.setup_owner(AuthSetupRequest(username=f"owner{index}",
                password=PASSWORD, setup_token=secret))
            return result.status.user.id
        except AuthError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert results.count("setup_already_complete") == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AccountRecord)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_other_service_instance_observes_revocation_and_permission_changes(auth):
    database, service, _, token_path = auth
    result = setup(service, token_path)
    second = AuthenticationService(database, password_hasher=service.password_hasher, clock=service.clock)
    assert second.authenticate(result.token, touch=False).user_id == result.status.user.id
    service.logout(result.token)
    with pytest.raises(AuthError):
        second.authenticate(result.token, touch=False)
    result = service.login(AuthLoginRequest(username="owner", password=PASSWORD))
    with database.session() as session:
        membership = session.scalar(select(Membership).where(Membership.user_id == result.status.user.id))
        session.delete(membership)
    with pytest.raises(AuthError, match="工作区不可用"):
        second.authenticate(result.token, touch=False)


def test_registration_policy_is_admin_only_and_closed_blocks_new_accounts(auth):
    _, service, _, token_path = auth
    owner = setup(service, token_path)
    friend = register(service)
    with pytest.raises(AuthError, match="管理员"):
        service.update_registration(service.authenticate(friend.token), AuthRegistrationSettings(registration_mode="closed"))
    service.update_registration(service.authenticate(owner.token), AuthRegistrationSettings(registration_mode="closed"))
    assert service.session_status().registration_mode == "closed"
    with pytest.raises(AuthError, match="已关闭"):
        register(service, "next_friend")
    assert service.login(AuthLoginRequest(username="friend", password=PASSWORD)).status.authenticated


def test_http_cookies_origin_csrf_and_validation_never_echo_passwords(auth):
    _, service, _, token_path = auth
    app = FastAPI()
    app.include_router(create_auth_router(lambda: service))
    with TestClient(app, base_url="http://testserver") as client:
        raw = token_path.read_text().strip()
        payload = {"username": "owner", "password": PASSWORD, "setup_token": raw}
        assert client.post("/api/v1/auth/setup", json=payload).status_code == 403
        response = client.post("/api/v1/auth/setup", json=payload, headers={"Origin": "http://testserver"})
        assert response.status_code == 201
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie and "Secure" not in cookie
        assert SESSION_COOKIE in client.cookies
        assert raw not in response.text and PASSWORD not in response.text
        assert client.cookies.get(SESSION_COOKIE) not in response.text
        status = client.get("/api/v1/auth/session").json()
        assert status["authenticated"] and status["user"]["username"] == "owner"
        assert client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"}).status_code == 403
        response = client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver", "X-CSRF-Token": status["csrf_token"]})
        assert response.status_code == 200 and not response.json()["authenticated"]
        assert SESSION_COOKIE not in client.cookies
        secret = "do-not-echo-this-password"
        bad = client.post("/api/v1/auth/register", json={"username": "bad/name", "password": secret}, headers={"Origin": "http://testserver"})
        assert bad.status_code == 422 and secret not in bad.text and "input" not in bad.text


def test_https_session_cookie_is_secure_and_auth_schemas_have_no_runtime_imports(auth):
    _, service, _, token_path = auth
    app = FastAPI()
    app.include_router(create_auth_router(lambda: service))
    with TestClient(app, base_url="https://example.test") as client:
        response = client.post("/api/v1/auth/setup", json={"username": "owner", "password": PASSWORD,
            "setup_token": token_path.read_text().strip()}, headers={"Origin": "https://example.test"})
        assert response.status_code == 201 and "; Secure" in response.headers["set-cookie"]
        assert "password" not in json.dumps(response.json())


def test_production_password_hashing_uses_argon2id_with_bounded_parallelism(auth):
    database, _, _, _ = auth
    service = AuthenticationService(database)
    assert service.password_hasher.memory_cost == 65536
    assert service.password_hasher.time_cost == 3 and service.password_hasher.parallelism == 1
    assert service._dummy_hash.startswith("$argon2id$v=19$m=65536,t=3,p=1$")
