"""Explicit SQLite setup and short transaction scopes; no import-time I/O."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.orm import Session, sessionmaker

from .seeds import LocalWorkspace, ensure_local_workspace


class Database:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000):
        if str(path) == ":memory:":
            raise ValueError("the Web store requires a persistent SQLite file")
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = Path(path).expanduser().resolve()
        self.engine: Engine = create_engine(
            URL.create("sqlite", database=str(self.path)),
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit on success, roll back on error, and always close the session."""
        with self._sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def migrate(self) -> None:
        """Explicitly create the parent folder/file and upgrade the schema."""
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "head")
        self.path.chmod(0o600)

    def local_workspace(self) -> LocalWorkspace:
        with self.session() as session:
            return ensure_local_workspace(session)

    def schema_revision(self) -> str | None:
        with self.engine.connect() as connection:
            return connection.scalar(text("SELECT version_num FROM alembic_version"))

    def dispose(self) -> None:
        self.engine.dispose()


def migration_config(connection: Connection | None = None) -> Config:
    """Resolve packaged migration scripts without depending on the process cwd."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def initialize_database(path: str | Path) -> Database:
    database = Database(path)
    try:
        database.migrate()
        database.local_workspace()
    except BaseException:
        database.dispose()
        raise
    return database
