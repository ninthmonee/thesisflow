# TradingAgents backend

Python application, CLI, API, workers, data adapters, migrations and tests live here.
The React application and legacy browser sources live in `../frontend` and
`../frontend/legacy`, shared API contracts in `../contracts`,
and local workbench data in `../data/web/development`.

With Python 3.13, Node 24 and uv installed, run from the repository root:

```bash
uv venv --python 3.13 backend/.venv
uv pip install --python backend/.venv/bin/python -e 'backend[web,dev]'
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run vendor:patch
backend/.venv/bin/python backend/scripts/export_openapi.py --check
backend/.venv/bin/python backend/scripts/package_workbench.py
./start-workbench.command
```

Run backend checks from this directory:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Private environment configuration belongs in `backend/.env`. Keys entered in the
web settings use the operating-system keyring. Neither is part of source control.
See the repository's `docs/` for product design, data-source research and status.

`scripts/package_workbench.py` builds the React application and copies its output
and the legacy sources into `tradingagents/web/ui/` and `tradingagents/web/static/`.
These directories contain generated distribution assets; edit browser sources only
under `frontend/`, then package them again. Release and container builds package
both interfaces before building the Python distribution.
