"""A recovery drill uses a new directory, restores artifacts and requires fresh credentials."""

import hashlib
import sqlite3
import zipfile

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from tradingagents.web.api.auth_schemas import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthRegistrationSettings,
    AuthSetupRequest,
)
from tradingagents.web.application.authentication import (
    SETUP_TOKEN_FILE,
    AuthenticationService,
    AuthError,
)
from tradingagents.web.application.research import ResearchService
from tradingagents.web.infrastructure.auth_models import (
    AccountRecord,
    AccountSessionRecord,
    AuthenticationRateLimitRecord,
    AuthenticationSettingsRecord,
)
from tradingagents.web.infrastructure.backup_bundle import (
    create_bundle,
    restore_bundle,
    verify_bundle,
)
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import ModelConnection, Report


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "state"
    database = initialize_database(root / "workbench.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, root / "research")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    run = service.create_run({"ticker": "NVDA", "date": "2024-01-02"}, **scope)
    service.tasks.finish(service.tasks.claim_next("test"), "completed")
    report = root / "research" / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Saved report")
    (root / ".env").write_text("DO-NOT-READ-OR-PACKAGE")
    with database.session() as session:
        session.add(ModelConnection(id="connection", **scope, name="saved", provider="openai", credential_ref="opaque-ref"))
        session.add(Report(workspace_id=local.workspace_id, run_id=run["id"], kind="_export_markdown",
            file_path="report.md", content_hash=hashlib.sha256(report.read_bytes()).hexdigest()))
    yield root, database, service, scope
    database.dispose()


def test_bundle_restore_drill_preserves_data_but_disables_account_reuse(state, tmp_path):
    root, database, _service, _scope = state
    bundle = tmp_path / "recovery.zip"
    result = create_bundle(root, bundle)
    assert result["credentials_included"] is False and bundle.stat().st_mode & 0o777 == 0o600
    metadata = verify_bundle(bundle)
    assert metadata["schema_revision"] == "0012_agent_runtimes"
    with zipfile.ZipFile(bundle) as archive:
        assert ".env" not in archive.namelist()
        assert all(b"DO-NOT-READ-OR-PACKAGE" not in archive.read(name) for name in archive.namelist())
    destination = tmp_path / "restored"
    restored = restore_bundle(bundle, destination)
    assert restored["credentials_rebind_required"] and not restored["services_started"]
    assert (destination / "research" / "report.md").read_text() == "Saved report"
    with sqlite3.connect(destination / "workbench.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT enabled,credential_ref FROM model_connections").fetchone() == (0, None)
        assert connection.execute("SELECT file_path FROM reports").fetchone()[0] == str(destination / "research" / "report.md")
    with database.engine.connect() as connection:
        from sqlalchemy import text
        assert connection.execute(text("SELECT enabled,credential_ref FROM model_connections")).one() == (1, "opaque-ref")
    with pytest.raises(FileExistsError):
        restore_bundle(bundle, destination)


def test_backup_refuses_pending_research_and_incomplete_references(state, tmp_path):
    root, _database, service, scope = state
    service.create_run({"ticker": "NVDA", "date": "2024-01-03"}, **scope)
    with pytest.raises(ValueError, match="queued/running"):
        create_bundle(root, tmp_path / "pending.zip")
    assert not (tmp_path / "pending.zip").exists()


def test_verify_rejects_hash_changes_and_path_traversal(state, tmp_path):
    root, _, _, _ = state
    original = tmp_path / "good.zip"
    create_bundle(root, original)
    corrupt = tmp_path / "bad.zip"
    with zipfile.ZipFile(original) as reader, zipfile.ZipFile(corrupt, "w") as writer:
        for name in reader.namelist():
            writer.writestr(name, b"bad" if name.endswith("report.md") else reader.read(name))
    with pytest.raises(ValueError, match="hash"):
        restore_bundle(corrupt, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()
    with zipfile.ZipFile(corrupt, "a") as writer:
        writer.writestr("../escape", "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        verify_bundle(corrupt)


def test_restore_preserves_account_ownership_and_password_hashes_but_revokes_browser_sessions(state, tmp_path):
    root, database, _service, scope = state
    password = "recovery-test-only password 123"
    hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    auth = AuthenticationService(database, password_hasher=hasher)
    owner = auth.setup_owner(AuthSetupRequest(username="owner", password=password,
        setup_token=auth.ensure_setup_token().read_text().strip()))
    friend = auth.register(AuthRegisterRequest(username="friend", password=password))
    second_owner = auth.login(AuthLoginRequest(username="owner", password=password))
    auth.update_registration(auth.authenticate(owner.token), AuthRegistrationSettings(registration_mode="closed"))
    with pytest.raises(AuthError):
        auth.login(AuthLoginRequest(username="owner", password="incorrect test password"))
    with database.session() as session:
        account_rows = [tuple(getattr(row, column) for column in (
            "user_id", "username", "workspace_id", "password_hash", "is_admin", "disabled"))
            for row in session.scalars(select(AccountRecord).order_by(AccountRecord.user_id)).all()]
        assert session.scalar(select(func.count()).select_from(AccountSessionRecord)) == 3
        assert session.scalar(select(func.count()).select_from(AuthenticationRateLimitRecord)) > 0
    bundle, destination = tmp_path / "accounts.zip", tmp_path / "recovered-accounts"
    create_bundle(root, bundle)
    restore_bundle(bundle, destination)
    restored = initialize_database(destination / "workbench.sqlite3")
    try:
        with restored.session() as session:
            recovered_rows = [tuple(getattr(row, column) for column in (
                "user_id", "username", "workspace_id", "password_hash", "is_admin", "disabled"))
                for row in session.scalars(select(AccountRecord).order_by(AccountRecord.user_id)).all()]
            assert recovered_rows == account_rows
            assert session.scalar(select(func.count()).select_from(AccountSessionRecord)) == 0
            assert session.scalar(select(func.count()).select_from(AuthenticationRateLimitRecord)) == 0
            settings = session.get(AuthenticationSettingsRecord, "instance")
            assert settings.owner_user_id == scope["owner_id"] and settings.registration_mode == "closed"
            assert settings.setup_token_digest is None and settings.setup_created_at is None
        recovered_auth = AuthenticationService(restored, password_hasher=hasher)
        assert not recovered_auth.session_status().setup_required
        assert recovered_auth.ensure_setup_token() is None
        for previous in (owner, friend, second_owner):
            with pytest.raises(AuthError) as revoked:
                recovered_auth.authenticate(previous.token)
            assert revoked.value.status_code == 401
            assert auth.authenticate(previous.token).user_id == previous.status.user.id
        new_owner = recovered_auth.login(AuthLoginRequest(username="owner", password=password))
        new_friend = recovered_auth.login(AuthLoginRequest(username="friend", password=password))
        assert new_owner.status.user.id == scope["owner_id"]
        assert new_owner.status.user.workspace.id == scope["workspace_id"]
        assert new_owner.status.user.is_admin
        assert new_friend.status.user.id == friend.status.user.id
        assert new_friend.status.user.workspace.id == friend.status.user.workspace.id
        assert not new_friend.status.user.is_admin
        assert new_owner.token not in {owner.token, second_owner.token, friend.token}
        with pytest.raises(AuthError, match="已关闭"):
            recovered_auth.register(AuthRegisterRequest(username="newfriend", password=password))
    finally:
        restored.dispose()


def test_restore_discards_unclaimed_setup_capability_and_requires_a_new_private_token(state, tmp_path):
    root, database, _service, scope = state
    hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    auth = AuthenticationService(database, password_hasher=hasher)
    old_token = auth.ensure_setup_token().read_text().strip()
    with database.session() as session:
        old_digest = session.get(AuthenticationSettingsRecord, "instance").setup_token_digest
        assert old_digest
    bundle, destination = tmp_path / "not-initialized.zip", tmp_path / "recovered-unclaimed"
    create_bundle(root, bundle)
    with zipfile.ZipFile(bundle) as archive:
        assert not any(SETUP_TOKEN_FILE in name for name in archive.namelist())
    restore_bundle(bundle, destination)
    assert not (destination / SETUP_TOKEN_FILE).exists()
    restored = initialize_database(destination / "workbench.sqlite3")
    try:
        with restored.session() as session:
            settings = session.get(AuthenticationSettingsRecord, "instance")
            assert settings.setup_token_digest is None and settings.setup_created_at is None
            assert settings.owner_user_id is None
        recovered_auth = AuthenticationService(restored, password_hasher=hasher)
        assert recovered_auth.session_status().setup_required
        body = {"username": "newowner", "password": "recovery-test-only password 123"}
        with pytest.raises(AuthError) as stale:
            recovered_auth.setup_owner(AuthSetupRequest(**body, setup_token=old_token))
        assert stale.value.code == "invalid_setup_token"
        token_path = recovered_auth.ensure_setup_token()
        assert token_path.stat().st_mode & 0o777 == 0o600
        new_token = token_path.read_text().strip()
        assert new_token != old_token
        with pytest.raises(AuthError):
            recovered_auth.setup_owner(AuthSetupRequest(**body, setup_token=old_token))
        claimed = recovered_auth.setup_owner(AuthSetupRequest(**body, setup_token=new_token))
        assert claimed.status.user.id == scope["owner_id"]
        assert claimed.status.user.workspace.id == scope["workspace_id"]
        with database.session() as session:
            assert session.get(AuthenticationSettingsRecord, "instance").setup_token_digest == old_digest
    finally:
        restored.dispose()
