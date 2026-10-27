"""Persistent data settings and cache cleanup never touch research or credentials."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select

from tradingagents.web.api.data_schemas import DataRuntimeSettings, DataSettingsError
from tradingagents.web.api.router import create_api_router, create_schema_app
from tradingagents.web.application.data_settings import DataSettingsService
from tradingagents.web.application.market_data import MarketDataService
from tradingagents.web.infrastructure.data_settings_model import DataSettingsRecord
from tradingagents.web.infrastructure.database import (
    Database,
    initialize_database,
    migration_config,
)
from tradingagents.web.infrastructure.models import Membership, User, Workspace


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "database.sqlite3")
    local = database.local_workspace()
    yield database, local, tmp_path
    database.dispose()


def test_incremental_migration_preserves_revision_one_data(tmp_path):
    database = Database(tmp_path / "upgrade.sqlite3")
    try:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), "0001_web_storage")
        assert "data_settings" not in inspect(database.engine).get_table_names()
        with database.session() as session:
            session.add(User(id="old-user", name="preserved"))
        database.migrate()
        database.migrate()
        assert database.schema_revision() == "0011_accounts_sessions"
        with database.session() as session:
            assert session.get(User, "old-user").name == "preserved"
            assert list(session.scalars(select(DataSettingsRecord))) == []
    finally:
        database.dispose()


def test_constructor_and_read_do_not_write_settings(context):
    database, local, root = context
    applications = []
    service = DataSettingsService(
        database, local, root / "missing-cache", apply_callback=applications.append
    )
    assert not (root / "missing-cache").exists()
    settings = service.get_settings()
    assert settings.revision == 0 and settings.updated_at is None
    assert settings.settings.minute_cache_ttl_seconds == 30
    assert settings.settings.daily_cache_ttl_seconds == 180
    assert applications == []
    with database.session() as session:
        assert session.get(DataSettingsRecord, local.workspace_id) is None
    assert service.apply_saved_settings().activation == "active"
    assert len(applications) == 1


def test_settings_survive_restart_and_reject_stale_revision(context):
    database, local, root = context
    values = DataRuntimeSettings(aggregation_max_workers=2, minute_cache_ttl_seconds=60)
    seen = []
    service = DataSettingsService(database, local, root / "cache", apply_callback=seen.append)
    saved = service.update_settings({"revision": 0, "settings": values.model_dump()})
    assert saved.revision == 1 and saved.activation == "active"
    assert seen == [values]
    restarted = DataSettingsService(database, local, root / "cache")
    assert restarted.get_settings().settings == values
    assert restarted.get_settings().activation == "restart_required"
    with pytest.raises(DataSettingsError) as captured:
        restarted.update_settings({"revision": 0, "settings": DataRuntimeSettings().model_dump()})
    assert captured.value.code == "data_settings_revision_conflict"
    assert restarted.get_settings().settings == values
    saved_again = restarted.update_settings({"revision": 1, "settings": values.model_dump()})
    assert saved_again.revision == 2 and saved_again.updated_at.tzinfo is UTC


@pytest.mark.parametrize(
    "field,value",
    [
        ("minute_cache_ttl_seconds", 4),
        ("minute_cache_ttl_seconds", 301),
        ("daily_cache_ttl_seconds", 29),
        ("daily_cache_ttl_seconds", 3601),
        ("aggregation_max_workers", 9),
        ("aggregation_max_workers", True),
        ("aggregation_timeout_seconds", 4),
        ("aggregation_refresh_seconds", 29),
    ],
)
def test_runtime_limits_are_strict_and_bounded(field, value):
    with pytest.raises(ValidationError):
        DataRuntimeSettings.model_validate({field: value})


def test_only_workspace_owner_can_change_settings_or_clear_cache(context):
    database, local, root = context
    with database.session() as session:
        session.add(User(id="reader", name="viewer"))
        session.flush()
        session.add(Membership(workspace_id=local.workspace_id, user_id="reader", role="viewer"))
    viewer = DataSettingsService(database, replace(local, user_id="reader"), root / "cache")
    assert viewer.get_settings().revision == 0
    for operation in (
        viewer.clear_market_cache,
        viewer.apply_saved_settings,
        lambda: viewer.update_settings({"revision": 0, "settings": {}}),
    ):
        with pytest.raises(DataSettingsError) as captured:
            operation()
        assert captured.value.status_code == 403
    unknown = DataSettingsService(database, replace(local, workspace_id="unknown"), root / "cache")
    with pytest.raises(DataSettingsError):
        unknown.get_settings()


def test_workspace_preferences_are_isolated(context):
    database, local, root = context
    with database.session() as session:
        session.add(Workspace(id="second", owner_id=local.user_id, name="second"))
        session.flush()
        session.add(Membership(workspace_id="second", user_id=local.user_id, role="owner"))
    first = DataSettingsService(database, local, root / "first")
    second = DataSettingsService(database, replace(local, workspace_id="second"), root / "second")
    first.update_settings({"revision": 0, "settings": {"aggregation_max_workers": 2}})
    assert second.get_settings().settings.aggregation_max_workers == 4
    assert second.get_settings().revision == 0


def test_activation_failure_is_safe_and_saved_pending_can_retry(context):
    database, local, root = context

    def failed(_settings):
        raise RuntimeError("secret /absolute/private/path")

    service = DataSettingsService(database, local, root / "cache", apply_callback=failed)
    saved = service.update_settings({"revision": 0, "settings": {}})
    assert saved.activation == "restart_required"
    assert "secret" not in saved.model_dump_json()
    service.apply_callback = lambda _settings: False
    assert service.apply_saved_settings().activation == "saved_pending"
    service.apply_callback = lambda _settings: True
    assert service.apply_saved_settings().activation == "active"


def test_status_reports_only_environment_presence_and_actual_capabilities(context):
    database, local, root = context
    keys = []

    def environment(key):
        keys.append(key)
        return "PRIVATE-KEY-DO-NOT-SERIALIZE" if key == "FRED_API_KEY" else None

    data = MarketDataService(root / "cache", provider=object())
    service = DataSettingsService(
        database,
        local,
        root / "cache",
        capabilities_reader=data.capabilities,
        environment_reader=environment,
        clock=lambda: datetime(2026, 10, 25, tzinfo=UTC),
    )
    status = service.get_status()
    assert keys == [
        "FRED_API_KEY",
        "ALPHA_VANTAGE_API_KEY",
        "TYPESAFE_API_KEY",
        "SEC_EDGAR_USER_AGENT",
    ]
    assert status.legacy_sources[0].environment_configured is True
    assert all(
        not source.market_feed_enabled and not source.connection_verified
        for source in status.legacy_sources
    )
    assert status.market_provider.realtime_guaranteed is False
    assert status.market_provider.nbbo is False
    assert (
        "PRIVATE-KEY" not in status.model_dump_json() and str(root) not in status.model_dump_json()
    )


def test_clear_only_removes_owned_bar_files_and_retains_frozen_evidence(context):
    database, local, root = context
    bars, snapshots = root / "market" / "bars", root / "snapshots"
    bars.mkdir(parents=True)
    snapshots.mkdir()
    owned = bars / ("a" * 64 + ".parquet")
    owned.write_bytes(b"public-bars")
    (bars / "unrelated.parquet").write_bytes(b"preserve")
    frozen = snapshots / ("mkt-" + "b" * 32 + ".json")
    frozen.write_bytes(b"frozen-evidence")
    session_file = root / "market" / "yahoo-runtime" / "cookies.db"
    session_file.parent.mkdir()
    session_file.write_bytes(b"provider-session")
    research = root / "research" / "report.md"
    research.parent.mkdir()
    research.write_bytes(b"research")
    invalidated = []
    service = DataSettingsService(
        database,
        local,
        root / "market",
        snapshot_cache_root=snapshots,
        cache_invalidator=lambda: invalidated.append(True),
    )
    stats = service.get_status().cache
    assert stats[0].file_count == 1 and stats[0].bytes == len(b"public-bars")
    assert stats[1].file_count == 1 and stats[1].clearable is False
    result = service.clear_market_cache()
    assert result.removed_file_count == 1 and result.removed_bytes == len(b"public-bars")
    assert result.in_memory_invalidated and invalidated == [True]
    assert not owned.exists()
    assert frozen.read_bytes() == b"frozen-evidence"
    assert session_file.read_bytes() == b"provider-session"
    assert research.read_bytes() == b"research"
    assert (bars / "unrelated.parquet").exists()
    assert str(root) not in result.model_dump_json()


def test_cleanup_never_follows_symlinks(context):
    database, local, root = context
    private = root / "private"
    private.mkdir()
    target = private / ("a" * 64 + ".parquet")
    target.write_bytes(b"do-not-delete")
    cache = root / "market"
    cache.mkdir()
    (cache / "bars").symlink_to(private, target_is_directory=True)
    service = DataSettingsService(database, local, cache)
    assert service.clear_market_cache().removed_file_count == 0
    assert service.get_status().cache[0].file_count == 0
    assert target.exists()
    (cache / "bars").unlink()
    (cache / "bars").mkdir()
    (cache / "bars" / ("b" * 64 + ".parquet")).symlink_to(target)
    assert service.clear_market_cache().removed_file_count == 0
    assert target.exists()


def test_http_routes_validate_revision_and_do_not_accept_paths(context):
    database, local, root = context
    service = DataSettingsService(database, local, root / "cache")
    app = FastAPI()
    app.include_router(create_api_router(data_service_reader=lambda: service))
    with TestClient(app) as client:
        assert client.get("/api/v1/data-settings").json()["revision"] == 0
        first = client.put("/api/v1/data-settings", json={"revision": 0, "settings": {}})
        assert first.status_code == 200 and first.json()["revision"] == 1
        assert (
            client.put("/api/v1/data-settings", json={"revision": 0, "settings": {}}).status_code
            == 409
        )
        assert (
            client.put(
                "/api/v1/data-settings", json={"revision": 1, "settings": {"path": "/"}}
            ).status_code
            == 422
        )
        assert client.get("/api/v1/data-status").status_code == 200
        assert client.post("/api/v1/data-settings/apply").status_code == 200
        assert client.post("/api/v1/data-cache/clear").status_code == 200
    schema = create_schema_app().openapi()
    assert schema["paths"]["/api/v1/data-settings"]["put"]["operationId"] == "updateDataSettings"
