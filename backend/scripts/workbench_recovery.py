"""Create, verify or restore a private recovery bundle without starting services."""

import argparse
import json

from tradingagents.web.infrastructure.backup_bundle import (
    create_bundle,
    restore_bundle,
    verify_bundle,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--state-root", required=True)
    backup.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--bundle", required=True)
    restore.add_argument("--destination", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "backup":
            result = create_bundle(arguments.state_root, arguments.output)
        elif arguments.command == "restore":
            result = restore_bundle(arguments.bundle, arguments.destination)
        else:
            manifest = verify_bundle(arguments.bundle)
            result = {"verified": True, "schema_revision": manifest["schema_revision"], "files": len(manifest["files"])}
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Recovery refused: {error}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
