#!/usr/bin/env python3
"""Export contracts without executing tradingagents' top-level dotenv side effect."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent


def contract_document() -> dict:
    # Normal package imports execute tradingagents/__init__.py and load .env.
    # A private package namespace keeps relative API imports intact without
    # importing the runtime package or instantiating its RunStore.
    package_dir = ROOT / "tradingagents" / "web" / "api"
    name = "_tradingagents_offline_api_contract"
    spec = importlib.util.spec_from_file_location(
        name, package_dir / "__init__.py", submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load API contract module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.create_schema_app().openapi()


def canonical_contract() -> str:
    return json.dumps(contract_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "contracts" / "openapi.json")
    parser.add_argument("--check", action="store_true", help="Fail on drift without writing files")
    args = parser.parse_args()
    content = canonical_contract()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != content:
            print("OpenAPI contract drift: run backend/scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("OpenAPI contract is current")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"Exported OpenAPI contract: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
