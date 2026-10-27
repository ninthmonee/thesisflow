"""Fingerprint executable research sources and serialization dependencies, without user data."""

import hashlib
import json
import sys
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from tradingagents.web.domain.tasks import ResearchError


def build_graph_contract(
    package_root: Path, dependencies: dict[str, str], python_version: str
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {"dependencies": dependencies, "python": python_version},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    sources = sorted(
        path
        for path in package_root.rglob("*.py")
        if not path.is_symlink() and "ui" not in path.relative_to(package_root).parts
    )
    if not sources:
        raise RuntimeError("Research source files are unavailable")
    for path in sources:
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "web-research-v2:" + digest.hexdigest()


@lru_cache(maxsize=1)
def current_graph_contract() -> str:
    dependencies = {}
    for name in (
        "langgraph",
        "langgraph-checkpoint",
        "langgraph-checkpoint-sqlite",
        "langchain-core",
        "pydantic",
    ):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = "unavailable"
    package_root = Path(__file__).resolve().parents[2]
    return build_graph_contract(
        package_root, dependencies, ".".join(map(str, sys.version_info[:3]))
    )


def verify_graph_contract(manifest: dict) -> None:
    if manifest.get("graph_contract_version") != current_graph_contract():
        raise ResearchError(
            "graph_contract_incompatible",
            "研究代码、提示词或运行依赖版本已改变，不能继续旧任务，请重新研究。",
            409,
        )
