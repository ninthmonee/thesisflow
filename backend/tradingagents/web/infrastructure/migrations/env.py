"""Alembic runtime; a database path is always supplied explicitly."""

from alembic import context

from tradingagents.web.infrastructure.schema import Base


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def online():
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        run(supplied)
        return

    from tradingagents.web.infrastructure.database import Database

    database_path = context.get_x_argument(as_dictionary=True).get("database")
    if not database_path:
        raise RuntimeError(
            "pass an explicit database path with -x database=/absolute/path/app.sqlite3"
        )
    database = Database(database_path)
    database.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        with database.engine.begin() as connection:
            run(connection)
        database.path.chmod(0o600)
    finally:
        database.dispose()


if context.is_offline_mode():
    context.configure(url="sqlite://", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    online()
