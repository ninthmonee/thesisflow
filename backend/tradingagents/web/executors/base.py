"""Private worker-owned execution context; credentials never serialize."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(repr=False)
class ResearchExecutionContext:
    claim: Any
    manifest: dict
    manifest_hash: str
    config: dict
    options: dict
    callback: Any
    active: Any
    capture: Any


class ResearchExecutor(Protocol):
    def run(self, context: ResearchExecutionContext) -> None: ...
