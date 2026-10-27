"""Own one local API and worker while keeping runtime data outside the source tree."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--state-dir", type=Path, default=REPOSITORY / "data/web/development")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(("127.0.0.1", args.port)) == 0:
            print(f"端口已被使用：http://127.0.0.1:{args.port}/workbench/", file=sys.stderr)
            return 1
    if not (BACKEND / "tradingagents/web/ui/index.html").is_file():
        subprocess.run([sys.executable, str(BACKEND / "scripts/package_workbench.py")], check=True)

    from tradingagents.dataflows.files import locked
    from tradingagents.web.infrastructure.database import initialize_database

    state_dir = args.state_dir.expanduser().resolve()
    owned = state_dir / "web"
    owned.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(owned / "launcher", wait=False) as acquired:
        if not acquired:
            print("此状态目录已有工作台启动进程。", file=sys.stderr)
            return 1
        database = initialize_database(owned / "workbench.sqlite3")
        database.dispose()
        stop = threading.Event()
        for event in (signal.SIGINT, signal.SIGTERM):
            signal.signal(event, lambda _number, _frame: stop.set())
        children = []
        environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
        try:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tradingagents.web.workers.research",
                    "--database",
                    str(owned / "workbench.sqlite3"),
                    "--artifacts",
                    str(owned / "research"),
                ],
                cwd=BACKEND,
                env=environment,
            )
            children.append(worker)
            api = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tradingagents.web.server",
                    "--port",
                    str(args.port),
                    "--state-dir",
                    str(state_dir),
                    "--durable-research",
                ],
                cwd=BACKEND,
                env=environment,
            )
            children.append(api)
            while not stop.wait(0.25):
                if any(child.poll() is not None for child in children):
                    return next((child.returncode for child in children if child.returncode), 1)
            return 0
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
