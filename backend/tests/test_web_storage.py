"""Web storage invariants, isolated from the user's application data."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from tradingagents.web.infrastructure.database import (
    Database,
    initialize_database,
    migration_config,
)
from tradingagents.web.infrastructure.models import (
    Base,
    Instrument,
    Membership,
    ModelConnection,
    ModelProfile,
    ResearchRun,
    RunAttempt,
    RunEvent,
    User,
    Watchlist,
    WatchlistItem,
    Workspace,
)
from tradingagents.web.infrastructure.seeds import (
    LOCAL_USER_ID,
    LOCAL_WATCHLIST_ID,
    LOCAL_WORKSPACE_ID,
)
from tradingagents.web.infrastructure.theme_model import ThemeRecord


@pytest.fixture
def database(tmp_path):
    database = initialize_database(tmp_path / "store" / "app.sqlite3")
    yield database
    database.dispose()


def test_constructor_does_not_create_directories_or_database(tmp_path):
    path = tmp_path / "not-created" / "app.sqlite3"
    database = Database(path)
    try:
        assert not path.parent.exists()
    finally:
        database.dispose()


def test_migration_and_local_seed_are_idempotent(database):
    with database.session() as session:
        session.get(Workspace, LOCAL_WORKSPACE_ID).name = "个人研究"
    database.migrate()
    local = database.local_workspace()
    assert local.name == "个人研究"
    assert local.workspace_id == LOCAL_WORKSPACE_ID
    assert local.user_id == LOCAL_USER_ID
    assert local.watchlist_id == LOCAL_WATCHLIST_ID
    assert database.schema_revision() == "0012_agent_runtimes"
    with database.session() as session:
        for model in (User, Workspace, Membership, Watchlist):
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_theme_pin_migration_backfills_existing_rows_without_changing_revision(tmp_path):
    database = Database(tmp_path / "legacy.sqlite3")
    try:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), "0005_market_data_connections")
        local = database.local_workspace()
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO themes (id,workspace_id,owner_id,builtin_key,current_version,archived,revision,created_at,updated_at) VALUES (:id,:workspace,:owner,NULL,2,1,3,:stamp,:stamp)"
                ),
                {
                    "id": "legacy-theme",
                    "workspace": local.workspace_id,
                    "owner": local.user_id,
                    "stamp": "2026-10-25 00:00:00",
                },
            )
        database.migrate()
        with database.session() as session:
            record = session.get(ThemeRecord, "legacy-theme")
            assert record.pinned is False and record.pinned_at is None
            assert record.archived and record.current_version == 2 and record.revision == 3
    finally:
        database.dispose()


def test_frozen_migration_matches_current_metadata(database):
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
        assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())


def test_sqlite_connections_enable_wal_foreign_keys_and_timeout(database):
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA busy_timeout")) == 5000
    assert database.path.stat().st_mode & 0o777 == 0o600


def test_transaction_rolls_back_on_failure(database):
    with pytest.raises(RuntimeError), database.session() as session:
        session.add(User(id="rolled-back", name="temporary"))
        session.flush()
        raise RuntimeError("application operation failed")
    with database.session() as session:
        assert session.get(User, "rolled-back") is None


def test_watchlist_items_are_unique_and_foreign_keys_are_enforced(database):
    with database.session() as session:
        session.add(Instrument(id="nvda", symbol="NVDA", name="NVIDIA"))
    with database.session() as session:
        session.add(
            WatchlistItem(
                workspace_id=LOCAL_WORKSPACE_ID,
                watchlist_id=LOCAL_WATCHLIST_ID,
                instrument_id="nvda",
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            WatchlistItem(
                workspace_id=LOCAL_WORKSPACE_ID,
                watchlist_id=LOCAL_WATCHLIST_ID,
                instrument_id="nvda",
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            WatchlistItem(
                workspace_id=LOCAL_WORKSPACE_ID,
                watchlist_id=LOCAL_WATCHLIST_ID,
                instrument_id="missing",
            )
        )


def test_watchlist_item_cannot_claim_a_different_workspace(database):
    with database.session() as session:
        session.add(Workspace(id="other", owner_id=LOCAL_USER_ID, name="other"))
        session.add(Instrument(id="aapl", symbol="AAPL", name="Apple"))
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            WatchlistItem(
                workspace_id="other", watchlist_id=LOCAL_WATCHLIST_ID, instrument_id="aapl"
            )
        )


def test_model_profile_cannot_reference_connection_in_another_workspace(database):
    with database.session() as session:
        session.add(Workspace(id="other", owner_id=LOCAL_USER_ID, name="other"))
        session.add(
            ModelConnection(
                id="connection",
                workspace_id=LOCAL_WORKSPACE_ID,
                owner_id=LOCAL_USER_ID,
                name="account",
                provider="deepseek",
                credential_ref="keychain:connection",
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            ModelProfile(workspace_id="other", connection_id="connection", model_id="model")
        )
    columns = {
        column["name"] for column in inspect(database.engine).get_columns("model_connections")
    }
    assert "credential_ref" in columns
    assert not columns & {"api_key", "secret", "token"}


def make_run(run_id, **overrides):
    fields = {
        "id": run_id,
        "workspace_id": LOCAL_WORKSPACE_ID,
        "owner_id": LOCAL_USER_ID,
        "ticker": "NVDA",
        "analysis_date": date(2026, 10, 23),
        "request_hash": "a" * 64,
    }
    fields.update(overrides)
    return ResearchRun(**fields)


def test_idempotency_key_is_unique_per_workspace_and_owner(database):
    with database.session() as session:
        session.add(make_run("one", idempotency_key="submit-1"))
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(make_run("two", idempotency_key="submit-1"))
    with database.session() as session:
        session.add(make_run("without-key-1"))
        session.add(make_run("without-key-2"))


def test_event_sequence_and_attempt_are_scoped_to_the_same_run(database):
    now = datetime.now(UTC)
    with database.session() as session:
        session.add(make_run("one"))
        session.add(make_run("two"))
    with database.session() as session:
        session.add(
            RunAttempt(
                id="attempt",
                workspace_id=LOCAL_WORKSPACE_ID,
                run_id="one",
                attempt_no=1,
                lease_owner="worker",
                lease_token=1,
                lease_until=now + timedelta(seconds=30),
                heartbeat_at=now,
            )
        )
    with database.session() as session:
        session.add(
            RunEvent(
                workspace_id=LOCAL_WORKSPACE_ID,
                run_id="one",
                attempt_id="attempt",
                event_seq=1,
                event_type="stage",
                payload={"stage": "analysts"},
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            RunEvent(workspace_id=LOCAL_WORKSPACE_ID, run_id="one", event_seq=1, event_type="stage")
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            RunEvent(
                workspace_id=LOCAL_WORKSPACE_ID,
                run_id="two",
                attempt_id="attempt",
                event_seq=1,
                event_type="stage",
            )
        )


def test_timestamps_reject_naive_values_and_round_trip_as_utc(database):
    offset_time = datetime(2026, 10, 24, 8, tzinfo=timezone(timedelta(hours=8)))
    with database.session() as session:
        session.add(User(id="offset", name="offset", created_at=offset_time))
    with database.session() as session:
        stored = session.get(User, "offset")
        assert stored.created_at == datetime(2026, 10, 24, tzinfo=UTC)
        assert stored.created_at.tzinfo is UTC
    with (
        pytest.raises(StatementError, match="timestamps must include a timezone"),
        database.session() as session,
    ):
        session.add(User(id="naive", name="naive", created_at=datetime(2026, 10, 24)))


def test_separate_database_files_do_not_share_data(tmp_path):
    first = initialize_database(tmp_path / "first.sqlite3")
    second = initialize_database(tmp_path / "second.sqlite3")
    try:
        with first.session() as session:
            session.add(User(id="only-first", name="only-first"))
        with second.session() as session:
            assert session.get(User, "only-first") is None
    finally:
        first.dispose()
        second.dispose()


def test_migration_path_does_not_depend_on_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database = initialize_database(tmp_path / "isolated.sqlite3")
    try:
        assert database.schema_revision() == "0012_agent_runtimes"
    finally:
        database.dispose()
