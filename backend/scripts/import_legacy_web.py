"""Inspect or import old Web runs into the new local workbench without changing their source."""

import argparse
import json
from pathlib import Path

from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_legacy import import_legacy_runs
from tradingagents.web.infrastructure.database import initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Old Web directory containing UUID/run.json"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data/web/development",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Import validated records; default is a dry run"
    )
    args = parser.parse_args()
    destination = args.state_dir.expanduser().resolve() / "web"
    source = args.source.expanduser().resolve()
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        parser.error("Source and destination must be separate directories")
    database = initialize_database(destination / "workbench.sqlite3")
    try:
        scope = database.local_workspace()
        service = ResearchService(database, destination / "research")
        result = import_legacy_runs(
            service,
            args.source,
            workspace_id=scope.workspace_id,
            owner_id=scope.user_id,
            dry_run=not args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
