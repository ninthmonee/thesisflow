#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec "$PWD/backend/.venv/bin/python" "$PWD/backend/scripts/run_workbench.py" "$@"
