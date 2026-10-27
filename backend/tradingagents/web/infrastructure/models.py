"""Database entities; application services own authorization and state transitions."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores naive timestamps; callers always read and write aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Created:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Versioned(Created):
    revision: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class User(Created, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))


class Workspace(Created, Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))


class Membership(Created, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint("role IN ('owner', 'researcher', 'viewer')", name="role"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))


class Instrument(Created, Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("symbol", "exchange", "asset_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(240))
    asset_type: Mapped[str] = mapped_column(String(24), default="stock")
    exchange: Mapped[str] = mapped_column(String(40), default="")
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    timezone: Mapped[str] = mapped_column(String(80), default="America/New_York")


class SymbolAlias(Base):
    __tablename__ = "symbol_aliases"
    __table_args__ = (UniqueConstraint("source", "alias", "venue"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    source: Mapped[str] = mapped_column(String(60))
    alias: Mapped[str] = mapped_column(String(80))
    venue: Mapped[str] = mapped_column(String(40), default="")


class Watchlist(Versioned, Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "owner_id", "name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(default=0)


class WatchlistItem(Created, Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id"),
        ForeignKeyConstraint(
            ["watchlist_id", "workspace_id"], ["watchlists.id", "watchlists.workspace_id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    watchlist_id: Mapped[str] = mapped_column(String(36))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    position: Mapped[int] = mapped_column(default=0)


class ModelConnection(Versioned, Base):
    __tablename__ = "model_connections"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        CheckConstraint("scope IN ('private', 'workspace')", name="scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(60))
    vendor_name: Mapped[str | None] = mapped_column(String(120))
    api_protocol: Mapped[str] = mapped_column(String(40), default="auto", server_default="auto")
    auth_type: Mapped[str] = mapped_column(String(32), default="api_key")
    credential_ref: Mapped[str | None] = mapped_column(String(240))
    extra_header_ref: Mapped[str | None] = mapped_column(String(240))
    header_names: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    endpoint: Mapped[str | None] = mapped_column(String(1000))
    scope: Mapped[str] = mapped_column(String(20), default="private")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelProfile(Versioned, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["connection_id", "workspace_id"],
            ["model_connections.id", "model_connections.workspace_id"],
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    connection_id: Mapped[str] = mapped_column(String(36))
    model_id: Mapped[str] = mapped_column(String(240))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    test_status: Mapped[str] = mapped_column(String(24), default="untested")
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    config_hash: Mapped[str | None] = mapped_column(String(64))


class ResearchTemplateVersion(Created, Base):
    __tablename__ = "research_template_versions"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "template_id", "version"),
        CheckConstraint("version > 0", name="version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    template_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default="draft")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RoleBinding(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        UniqueConstraint("template_version_id", "role"),
        ForeignKeyConstraint(
            ["template_version_id", "workspace_id"],
            ["research_template_versions.id", "research_template_versions.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["model_profile_id", "workspace_id"],
            ["model_profiles.id", "model_profiles.workspace_id"],
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    template_version_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(80))
    model_profile_id: Mapped[str] = mapped_column(String(36))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResearchRun(Versioned, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "owner_id", "idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'cancelling', 'cancelled', "
            "'completed', 'failed', 'interrupted')",
            name="status",
        ),
        CheckConstraint(
            "data_quality IN ('complete', 'partial', 'unavailable')", name="data_quality"
        ),
        Index("ix_runs_status_created_at", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("instruments.id"))
    ticker: Mapped[str] = mapped_column(String(64))
    analysis_date: Mapped[date] = mapped_column(Date)
    as_of: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(24), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="initializing")
    data_quality: Mapped[str] = mapped_column(String(24), default="partial")
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    next_event_seq: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)


class RunManifest(Created, Base):
    __tablename__ = "run_manifests"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
    )
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class RunAttempt(Created, Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("id", "run_id", "workspace_id"),
        UniqueConstraint("run_id", "attempt_no"),
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
        CheckConstraint("attempt_no > 0 AND lease_token > 0", name="sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    run_id: Mapped[str] = mapped_column(String(36))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="running")
    lease_owner: Mapped[str] = mapped_column(String(160))
    lease_token: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime())
    checkpoint_ref: Mapped[str | None] = mapped_column(String(1000))
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "event_seq"),
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
        ForeignKeyConstraint(
            ["attempt_id", "run_id", "workspace_id"],
            ["run_attempts.id", "run_attempts.run_id", "run_attempts.workspace_id"],
        ),
        CheckConstraint("event_seq > 0", name="sequence"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    run_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    event_seq: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Report(Created, Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "version"),
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    run_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(default=1)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    content: Mapped[str | None] = mapped_column(Text)
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class DataSnapshot(Created, Base):
    __tablename__ = "data_snapshots"
    __table_args__ = (UniqueConstraint("id", "workspace_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    source: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(60))
    file_path: Mapped[str] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Evidence(Created, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
        ForeignKeyConstraint(
            ["snapshot_id", "workspace_id"], ["data_snapshots.id", "data_snapshots.workspace_id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    run_id: Mapped[str] = mapped_column(String(36))
    snapshot_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(240))
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class LegacyImport(Created, Base):
    __tablename__ = "legacy_imports"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_root_hash", "legacy_run_id", "source_hash"),
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_root_hash: Mapped[str] = mapped_column(String(64))
    legacy_run_id: Mapped[str] = mapped_column(String(80))
    source_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24))
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    file_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
