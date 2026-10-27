"""Password accounts and private workspaces, authenticated by revocable opaque cookies."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from tradingagents.web.api.auth_schemas import AuthSessionStatus, AuthUser, AuthWorkspace
from tradingagents.web.infrastructure.auth_models import (
    AccountRecord,
    AccountSessionRecord,
    AuthenticationRateLimitRecord,
    AuthenticationSettingsRecord,
)
from tradingagents.web.infrastructure.models import Membership, User, Watchlist, Workspace
from tradingagents.web.infrastructure.seeds import (
    LOCAL_USER_ID,
    LocalWorkspace,
    ensure_local_workspace,
)

SESSION_COOKIE = "ta_session"
SESSION_SECONDS = 7 * 24 * 3600
SESSION_IDLE_SECONDS = 12 * 3600
SETUP_TOKEN_FILE = "account-setup-token"
SETUP_TOKEN_SECONDS = 24 * 3600


class AuthError(Exception):
    def __init__(self, code, message, status_code=401):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    username: str
    display_name: str
    is_admin: bool
    workspace: LocalWorkspace
    session_id: str

    @property
    def is_legacy_owner(self):
        return self.is_admin and self.user_id == LOCAL_USER_ID

    @property
    def can_use_legacy_credentials(self):
        return self.is_legacy_owner


@dataclass(frozen=True)
class AuthResult:
    status: AuthSessionStatus
    token: str = field(repr=False)


def token_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for_token(value):
    return hmac.new(value.encode("utf-8"), b"tradingagents.csrf.v1", hashlib.sha256).hexdigest()


class AuthenticationService:
    def __init__(self, database, *, clock=None, setup_token_path=None, password_hasher=None,
                 session_seconds=SESSION_SECONDS, idle_seconds=SESSION_IDLE_SECONDS):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))
        self.setup_token_path = Path(setup_token_path or database.path.parent / SETUP_TOKEN_FILE)
        self.password_hasher = password_hasher or PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16, type=Type.ID
        )
        self.session_seconds, self.idle_seconds = session_seconds, idle_seconds
        if not 60 <= session_seconds <= SESSION_SECONDS or not 60 <= idle_seconds <= SESSION_IDLE_SECONDS:
            raise ValueError("invalid session lifetime")
        self._hash_slots = threading.BoundedSemaphore(2)
        self._dummy_hash = self.password_hasher.hash(secrets.token_urlsafe(32))

    def now(self):
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("Authentication clock must include timezone")
        return value.astimezone(UTC)

    @contextmanager
    def _write(self):
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            yield session

    @staticmethod
    def _settings(session, *, create=False):
        settings = session.get(AuthenticationSettingsRecord, "instance")
        if settings is None and create:
            settings = AuthenticationSettingsRecord(id="instance", registration_mode="open")
            session.add(settings)
            session.flush()
        return settings

    @staticmethod
    def _needs_setup(session):
        return not bool(session.scalar(select(func.count()).select_from(AccountRecord)))

    def ensure_setup_token(self, *, rotate=False):
        """Return its private file path; never return or log the bootstrap secret."""
        with self._write() as session:
            if not self._needs_setup(session):
                return None
            settings = self._settings(session, create=True)
            path = self.setup_token_path
            if path.is_symlink() or not path.parent.is_dir():
                raise AuthError("setup_token_unavailable", "初始化令牌文件不可用，请检查本机状态目录。", 503)
            if path.exists():
                if not path.is_file() or path.stat().st_size > 128 or path.stat().st_mode & 0o077:
                    raise AuthError("setup_token_unavailable", "初始化令牌文件权限无效，须仅当前本机用户可读。", 503)
                raw = path.read_text(encoding="utf-8").strip()
                valid = (settings.setup_token_digest and re.fullmatch(r"[A-Za-z0-9_-]{43}", raw)
                    and hmac.compare_digest(token_digest(raw), settings.setup_token_digest))
                expired = settings.setup_created_at is None or self.now() >= settings.setup_created_at + timedelta(seconds=SETUP_TOKEN_SECONDS)
                if valid and not expired and not rotate:
                    return path
                if not valid and not rotate:
                    raise AuthError("setup_token_unavailable", "初始化令牌文件与数据库不一致，请在本机控制台使用 --rotate 重新生成。", 503)
            value = secrets.token_urlsafe(32)
            temporary = path.with_name(f".{SETUP_TOKEN_FILE}-{uuid4()}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(value + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                settings.setup_token_digest = token_digest(value)
                settings.setup_created_at = self.now()
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            return path

    def _rate(self, action, key, *, limit, seconds):
        digest, now = token_digest(action + ":" + str(key)[:256]), self.now()
        with self._write() as session:
            session.execute(delete(AuthenticationRateLimitRecord).where(AuthenticationRateLimitRecord.expires_at <= now))
            row = session.get(AuthenticationRateLimitRecord, digest)
            if row is None:
                row = AuthenticationRateLimitRecord(key_digest=digest, attempts=1,
                    window_started_at=now, expires_at=now + timedelta(seconds=seconds))
                session.add(row)
            elif row.attempts >= limit:
                raise AuthError("auth_rate_limited", "尝试过于频繁，请稍后重试。", 429)
            else:
                row.attempts += 1

    @contextmanager
    def _hash_slot(self):
        if not self._hash_slots.acquire(blocking=False):
            raise AuthError("auth_busy", "认证服务正忙，请稍后重试。", 429)
        try:
            yield
        finally:
            self._hash_slots.release()

    def _hash_password(self, password):
        with self._hash_slot():
            return self.password_hasher.hash(password)

    def _verify_password(self, digest, password):
        with self._hash_slot():
            try:
                return self.password_hasher.verify(digest, password)
            except (VerificationError, InvalidHashError):
                return False

    @staticmethod
    def _require_admin(identity):
        if not identity.is_admin:
            raise AuthError("admin_required", "此操作需要管理员权限。", 403)

    def _issue(self, session, account, *, replaced_token=None):
        now, value = self.now(), secrets.token_urlsafe(32)
        # Only digest-indexed session rows are persisted; this token appears only in a cookie.
        if replaced_token and re.fullmatch(r"[A-Za-z0-9_-]{43}", replaced_token):
            old = session.scalar(select(AccountSessionRecord).where(AccountSessionRecord.token_digest == token_digest(replaced_token)))
            if old is not None:
                old.revoked_at = now
        # autoflush is disabled by the shared Database. Persist revocations before
        # bulk cleanup, otherwise ORM synchronization could discard pending writes.
        session.flush()
        # Keep each account's session storage bounded.
        session.execute(delete(AccountSessionRecord).where(
            AccountSessionRecord.user_id == account.user_id,
            (AccountSessionRecord.expires_at <= now) | AccountSessionRecord.revoked_at.is_not(None)))
        active = session.scalars(select(AccountSessionRecord).where(
            AccountSessionRecord.user_id == account.user_id,
            AccountSessionRecord.revoked_at.is_(None)).order_by(AccountSessionRecord.created_at.desc())).all()
        for old in active[9:]:
            old.revoked_at = now
        row = AccountSessionRecord(id=str(uuid4()), token_digest=token_digest(value),
            user_id=account.user_id, created_at=now, last_seen_at=now,
            expires_at=now + timedelta(seconds=self.session_seconds))
        session.add(row)
        session.flush()
        return value

    def setup_owner(self, body, *, client_key="local", replaced_token=None):
        self._rate("setup-ip", client_key, limit=10, seconds=900)
        digest = token_digest(body.setup_token.get_secret_value())
        with self.database.session() as session:
            settings = self._settings(session)
            if not self._needs_setup(session):
                raise AuthError("setup_already_complete", "管理员已初始化，请登录或注册独立账号。", 409)
            if (settings is None or not settings.setup_token_digest or settings.setup_created_at is None
                    or self.now() >= settings.setup_created_at + timedelta(seconds=SETUP_TOKEN_SECONDS)
                    or not hmac.compare_digest(settings.setup_token_digest, digest)):
                raise AuthError("invalid_setup_token", "初始化令牌无效，请从本机控制台取得令牌。", 403)
        password_hash = self._hash_password(body.password.get_secret_value())
        with self._write() as session:
            settings = self._settings(session)
            if not self._needs_setup(session):
                raise AuthError("setup_already_complete", "管理员已初始化，请登录。", 409)
            if (settings is None or not settings.setup_token_digest or settings.setup_created_at is None
                    or self.now() >= settings.setup_created_at + timedelta(seconds=SETUP_TOKEN_SECONDS)
                    or not hmac.compare_digest(settings.setup_token_digest, digest)):
                raise AuthError("invalid_setup_token", "初始化令牌无效。", 403)
            local = ensure_local_workspace(session)
            display_name = body.display_name or body.username
            session.get(User, local.user_id).name = display_name
            account = AccountRecord(user_id=local.user_id, username=body.username,
                workspace_id=local.workspace_id, password_hash=password_hash,
                is_admin=True, disabled=False, created_at=self.now(), password_changed_at=self.now())
            session.add(account)
            session.flush()
            settings.owner_user_id, settings.setup_token_digest = local.user_id, None
            token = self._issue(session, account, replaced_token=replaced_token)
        with suppress(OSError):
            if not self.setup_token_path.is_symlink():
                self.setup_token_path.unlink(missing_ok=True)
        return AuthResult(self.session_status(token), token)

    def register(self, body, *, client_key="local", replaced_token=None):
        self._rate("register-ip", client_key, limit=10, seconds=3600)
        with self.database.session() as session:
            if self._needs_setup(session):
                raise AuthError("owner_setup_required", "请先由本机所有者完成管理员初始化。", 409)
            settings = self._settings(session)
            if settings is None or settings.registration_mode != "open":
                raise AuthError("registration_closed", "当前已关闭新账号注册，请联系管理员。", 403)
            if session.scalar(select(AccountRecord.user_id).where(AccountRecord.username == body.username)):
                raise AuthError("username_unavailable", "用户名不可用，请换一个用户名。", 409)
        password_hash = self._hash_password(body.password.get_secret_value())
        now, user_id, workspace_id, watchlist_id = self.now(), str(uuid4()), str(uuid4()), str(uuid4())
        try:
            with self._write() as session:
                if self._needs_setup(session):
                    raise AuthError("owner_setup_required", "请先完成管理员初始化。", 409)
                settings = self._settings(session)
                if settings is None or settings.registration_mode != "open":
                    raise AuthError("registration_closed", "当前已关闭新账号注册。", 403)
                display = body.display_name or body.username
                session.add(User(id=user_id, name=display, created_at=now))
                session.flush()
                session.add(Workspace(id=workspace_id, owner_id=user_id, name=f"{display}的工作区", created_at=now))
                session.flush()
                session.add(Membership(id=str(uuid4()), workspace_id=workspace_id, user_id=user_id, role="owner", created_at=now))
                session.add(Watchlist(id=watchlist_id, workspace_id=workspace_id, owner_id=user_id,
                    name="我的自选", position=0, revision=1, created_at=now, updated_at=now))
                account = AccountRecord(user_id=user_id, username=body.username, workspace_id=workspace_id,
                    password_hash=password_hash, is_admin=False, disabled=False,
                    created_at=now, password_changed_at=now)
                session.add(account)
                session.flush()
                token = self._issue(session, account, replaced_token=replaced_token)
        except IntegrityError:
            raise AuthError("username_unavailable", "用户名不可用，请换一个用户名。", 409) from None
        return AuthResult(self.session_status(token), token)

    def login(self, body, *, client_key="local", replaced_token=None):
        self._rate("login-ip", client_key, limit=60, seconds=900)
        self._rate("login-account", body.username, limit=12, seconds=900)
        with self.database.session() as session:
            account = session.scalar(select(AccountRecord).where(AccountRecord.username == body.username))
            digest = account.password_hash if account else self._dummy_hash
            user_id = account.user_id if account else None
            disabled = account.disabled if account else True
        verified = self._verify_password(digest, body.password.get_secret_value())
        if not verified or disabled or user_id is None:
            raise AuthError("invalid_credentials", "用户名或密码不正确。", 401)
        replacement = self._hash_password(body.password.get_secret_value()) if self.password_hasher.check_needs_rehash(digest) else None
        with self._write() as session:
            account = session.get(AccountRecord, user_id)
            if account is None or account.disabled or account.password_hash != digest:
                raise AuthError("invalid_credentials", "用户名或密码不正确。", 401)
            if replacement:
                account.password_hash = replacement
            session.execute(delete(AuthenticationRateLimitRecord).where(
                AuthenticationRateLimitRecord.key_digest == token_digest("login-account:" + body.username)))
            token = self._issue(session, account, replaced_token=replaced_token)
        return AuthResult(self.session_status(token), token)

    def authenticate(self, raw_token, *, touch=True):
        if not isinstance(raw_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token):
            raise AuthError("authentication_required", "请先登录。", 401)
        now = self.now()
        with self.database.session() as session:
            row = session.scalar(select(AccountSessionRecord).where(AccountSessionRecord.token_digest == token_digest(raw_token)))
            if row is None or row.revoked_at is not None or row.expires_at <= now or row.last_seen_at + timedelta(seconds=self.idle_seconds) <= now:
                raise AuthError("session_expired", "登录已过期，请重新登录。", 401)
            account = session.get(AccountRecord, row.user_id)
            if account is None or account.disabled:
                raise AuthError("session_expired", "登录已过期，请重新登录。", 401)
            workspace = session.get(Workspace, account.workspace_id)
            membership = session.scalar(select(Membership).where(
                Membership.workspace_id == account.workspace_id, Membership.user_id == account.user_id))
            if workspace is None or workspace.owner_id != account.user_id or membership is None:
                raise AuthError("workspace_unavailable", "账号工作区不可用，请联系管理员。", 403)
            watchlist_id = session.scalar(select(Watchlist.id).where(
                Watchlist.workspace_id == workspace.id, Watchlist.owner_id == account.user_id
            ).order_by(Watchlist.position, Watchlist.created_at).limit(1))
            if touch and now - row.last_seen_at >= timedelta(seconds=60):
                row.last_seen_at = now
            return AuthIdentity(account.user_id, account.username, session.get(User, account.user_id).name,
                account.is_admin, LocalWorkspace(account.user_id, workspace.id, watchlist_id or "", workspace.name), row.id)

    def validate_csrf(self, raw_token, header):
        self.authenticate(raw_token, touch=False)
        if not isinstance(header, str) or len(header) != 64 or not hmac.compare_digest(csrf_for_token(raw_token), header):
            raise AuthError("csrf_validation_failed", "页面验证已失效，请刷新页面后重试。", 403)

    def session_status(self, raw_token=None):
        with self.database.session() as session:
            setup_required = self._needs_setup(session)
            settings = self._settings(session)
            mode = settings.registration_mode if settings else "open"
        try:
            identity = self.authenticate(raw_token, touch=False)
        except AuthError as error:
            if error.status_code not in {401, 403}:
                raise
            return AuthSessionStatus(authenticated=False, setup_required=setup_required, registration_mode=mode)
        with self.database.session() as session:
            row = session.get(AccountSessionRecord, identity.session_id)
            account = session.get(AccountRecord, identity.user_id)
            now = self.now()
            if (row is None or row.revoked_at is not None or row.expires_at <= now
                    or row.last_seen_at + timedelta(seconds=self.idle_seconds) <= now
                    or account is None or account.disabled):
                return AuthSessionStatus(authenticated=False, setup_required=setup_required, registration_mode=mode)
            expires_at = min(row.expires_at, row.last_seen_at + timedelta(seconds=self.idle_seconds))
        return AuthSessionStatus(authenticated=True, setup_required=False, registration_mode=mode,
            user=AuthUser(id=identity.user_id, username=identity.username, display_name=identity.display_name,
                is_admin=identity.is_admin, workspace=AuthWorkspace(id=identity.workspace.workspace_id,
                    name=identity.workspace.name, owner_id=identity.user_id)),
            csrf_token=csrf_for_token(raw_token), expires_at=expires_at)

    def logout(self, raw_token, *, all_sessions=False):
        with self._write() as session:
            row = session.scalar(select(AccountSessionRecord).where(AccountSessionRecord.token_digest == token_digest(raw_token or "")))
            if row is not None:
                if all_sessions:
                    for item in session.scalars(select(AccountSessionRecord).where(AccountSessionRecord.user_id == row.user_id)):
                        item.revoked_at = self.now()
                else:
                    row.revoked_at = self.now()
        return self.session_status()

    def change_password(self, raw_token, body):
        identity = self.authenticate(raw_token, touch=False)
        self._rate("password-account", identity.user_id, limit=10, seconds=900)
        with self.database.session() as session:
            digest = session.get(AccountRecord, identity.user_id).password_hash
        if not self._verify_password(digest, body.current_password.get_secret_value()):
            raise AuthError("invalid_credentials", "当前密码不正确。", 401)
        replacement = self._hash_password(body.new_password.get_secret_value())
        with self._write() as session:
            account = session.get(AccountRecord, identity.user_id)
            if account.password_hash != digest or account.disabled:
                raise AuthError("session_expired", "登录已失效，请重新登录。", 401)
            account.password_hash, account.password_changed_at = replacement, self.now()
            for row in session.scalars(select(AccountSessionRecord).where(AccountSessionRecord.user_id == identity.user_id)):
                row.revoked_at = self.now()
            token = self._issue(session, account)
        return AuthResult(self.session_status(token), token)

    def registration_settings(self, identity):
        self._require_admin(identity)
        with self.database.session() as session:
            settings = self._settings(session)
            return {"registration_mode": settings.registration_mode if settings else "open"}

    def update_registration(self, identity, body):
        self._require_admin(identity)
        with self._write() as session:
            settings = self._settings(session, create=True)
            settings.registration_mode = body.registration_mode
        return self.registration_settings(identity)
