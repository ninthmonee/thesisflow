"""Immutable, scoped SQLite checkpoint snapshots for fenced Web attempts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.web.domain.tasks import ResearchError, TaskClaim

if TYPE_CHECKING:
    from tradingagents.web.application.research import ResearchService


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> None:
    """Copy all committed checkpoint/writes tables, including their channel versions."""
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader,
        closing(sqlite3.connect(str(destination))) as writer,
    ):
        reader.backup(writer)
        writer.commit()
    destination.chmod(0o600)


class CheckpointFiles:
    def __init__(self, service: ResearchService):
        self.service = service

    def attempt_directory(self, claim: TaskClaim) -> Path:
        return (
            self.service.run_directory(claim.workspace_id, claim.run_id)
            / "attempts"
            / str(UUID(claim.attempt_id))
        )

    def working_database(self, claim: TaskClaim, ticker: str) -> Path:
        return (
            self.attempt_directory(claim)
            / "cache"
            / "checkpoints"
            / f"{safe_ticker_component(ticker).upper()}.db"
        )

    def validate_ref(
        self, reference: str, workspace_id: str, run_id: str, manifest_hash: str
    ) -> tuple[dict, Path]:
        try:
            relative = Path(reference)
            if relative.is_absolute():
                raise ValueError
            candidate = self.service.artifacts_root / relative
            if candidate.is_symlink():
                raise ValueError
            path = candidate.resolve()
            expected = self.service.run_directory(workspace_id, run_id)
            if not path.is_relative_to(expected) or not path.is_file():
                raise ValueError
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if (
                metadata["workspace_id"] != workspace_id
                or metadata["run_id"] != run_id
                or metadata["manifest_hash"] != manifest_hash
            ):
                raise ValueError
            name = metadata["database_file"]
            if not isinstance(name, str) or Path(name).name != name or not name.endswith(".db"):
                raise ValueError
            database = path.with_name(name)
            if database.is_symlink() or not database.is_file():
                raise ValueError
            if file_hash(database) != metadata["database_sha256"]:
                raise ValueError
            return metadata, database
        except (OSError, ValueError, KeyError, TypeError):
            raise ResearchError(
                "checkpoint_invalid", "检查点缺失、已改变或与当前研究不兼容"
            ) from None

    def restore(
        self, claim: TaskClaim, manifest_hash: str, ticker: str, signature: str
    ) -> dict | None:
        if not claim.checkpoint_ref:
            return None
        metadata, source = self.validate_ref(
            claim.checkpoint_ref, claim.workspace_id, claim.run_id, manifest_hash
        )
        if metadata.get("signature") != signature:
            raise ResearchError(
                "checkpoint_incompatible", "模型、提示词或执行图配置已改变，请重新研究"
            )
        backup_database(source, self.working_database(claim, ticker))
        return metadata

    def publish(
        self,
        claim: TaskClaim,
        manifest_hash: str,
        ticker: str,
        signature: str,
        thread_id: str | None,
    ) -> str | None:
        if thread_id is None:
            return None
        source = self.working_database(claim, ticker)
        if source.is_symlink() or not source.is_file():
            return None
        from langgraph.checkpoint.sqlite import SqliteSaver

        folder = self.attempt_directory(claim) / "checkpoint-snapshots"
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        stamp = str(uuid4())
        destination = folder / f"{stamp}.db"
        backup_database(source, destination)
        with closing(sqlite3.connect(str(destination), check_same_thread=False)) as connection:
            checkpoint = SqliteSaver(connection).get_tuple(
                {"configurable": {"thread_id": thread_id}}
            )
        if checkpoint is None:
            destination.unlink()
            return None
        metadata = {
            "workspace_id": claim.workspace_id,
            "run_id": claim.run_id,
            "source_attempt_id": claim.attempt_id,
            "manifest_hash": manifest_hash,
            "signature": signature,
            "thread_id": thread_id,
            "database_file": destination.name,
            "database_sha256": file_hash(destination),
            "published_at": self.service.tasks.now().isoformat(),
        }
        path = folder / f"{stamp}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return str(path.relative_to(self.service.artifacts_root))

    def prune_previous(self, claim: TaskClaim, keep_reference: str) -> None:
        """Only the current attempt's last accepted snapshot is needed for resume."""
        keep = (self.service.artifacts_root / keep_reference).resolve()
        folder = self.attempt_directory(claim) / "checkpoint-snapshots"
        for metadata in folder.glob("*.json"):
            if metadata.resolve() == keep or metadata.is_symlink():
                continue
            with suppress(OSError):
                metadata.with_suffix(".db").unlink(missing_ok=True)
                metadata.unlink()
