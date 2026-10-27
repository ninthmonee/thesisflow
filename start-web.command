#!/bin/zsh
set -e
cd -- "${0:A:h}"
export PATH="$PWD/backend/.venv/bin:$PATH"
if [[ ! -x "$PWD/backend/.venv/bin/tradingagents-web" ]]; then
  print "请先安装 Web 依赖：uv pip install --python backend/.venv/bin/python -e 'backend[web]'"
  exit 1
fi
cd backend
exec "$PWD/.venv/bin/tradingagents-web" "$@"
