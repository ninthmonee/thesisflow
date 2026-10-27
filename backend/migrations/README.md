**Web database migrations**

The revision sources live in `backend/tradingagents/web/infrastructure/migrations` from the repository root, so installed Web packages can upgrade a database without the repository checkout. The backend's `alembic.ini` resolves this location relative to itself, independently of the current directory.

Application setup explicitly calls `initialize_database(path)` from `tradingagents.web.infrastructure.database`. Importing the module does not create a database. Initialization upgrades the schema and idempotently creates the local user, workspace, membership and default watchlist.

From the repository root, schema-only upgrades can also be run with:

```sh
backend/.venv/bin/alembic -c backend/alembic.ini -x database=/absolute/path/app.sqlite3 upgrade head
```

The CLI requires an explicit path and only changes the schema. It does not seed identities, import old tasks or start a worker. Back up an existing database before a version upgrade; application code never automatically downgrades it.

The current revision chain is `0001_web_storage` → `0002_data_settings` → `0003_model_protocols` → `0004_themes` → `0005_market_data_connections`. It covers model protocol configuration, versioned workspace themes and their memberships, and alternative market-data connections in addition to the original research store.
