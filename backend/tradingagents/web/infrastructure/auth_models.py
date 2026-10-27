"""Account passwords and opaque session digests; no plaintext login secrets."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime


class AccountRecord(Base):
    __tablename__ = "auth_accounts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    password_hash: Mapped[str] = mapped_column(String(512))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AccountSessionRecord(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_accounts.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuthenticationSettingsRecord(Base):
    __tablename__ = "auth_settings"
    __table_args__ = (CheckConstraint("registration_mode IN ('open','closed')", name="registration_mode"),)
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    registration_mode: Mapped[str] = mapped_column(String(16), default="open")
    setup_token_digest: Mapped[str | None] = mapped_column(String(64))
    setup_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class AuthenticationRateLimitRecord(Base):
    __tablename__ = "auth_rate_limits"
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    attempts: Mapped[int] = mapped_column(Integer)
