#!/bin/zsh
set -e
cd -- "${0:A:h}"
cd backend
export PATH="$PWD/.venv/bin:$PATH"
exec "$PWD/.venv/bin/tradingagents" "$@"
