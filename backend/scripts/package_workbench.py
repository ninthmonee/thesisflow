"""Package React and legacy browser assets from frontend into the Python distribution."""

import argparse
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def package_workbench(*, skip_build: bool = False) -> Path:
    frontend = PROJECT_ROOT / "frontend"
    if not skip_build:
        subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
    source = frontend / "dist"
    if not (source / "index.html").is_file():
        raise FileNotFoundError("Build frontend/dist before packaging the workbench")
    legacy = frontend / "legacy"
    if not all((legacy / name).is_file() for name in ("index.html", "app.js", "app.css")):
        raise FileNotFoundError("Legacy browser sources are required in frontend/legacy")
    package_root = PROJECT_ROOT / "backend" / "tradingagents" / "web"
    assets = ((source, package_root / "ui"), (legacy, package_root / "static"))
    for _, destination in assets:
        if destination.is_symlink():
            raise ValueError("A packaged browser assets directory cannot be a symlink")
    for origin, destination in assets:
        # Dedicated generated-assets directories, never user-data paths.
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(origin, destination)
    return package_root / "ui"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    destination = package_workbench(skip_build=args.skip_build)
    print(f"Packaged workbench assets: {destination}")
    print(f"Packaged legacy assets: {destination.with_name('static')}")


if __name__ == "__main__":
    main()
