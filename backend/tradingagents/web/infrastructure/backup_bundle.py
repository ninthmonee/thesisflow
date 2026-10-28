"""Offline private recovery bundles. Never read Keychain, OAuth files or environment secrets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from tradingagents.web.infrastructure.recovery_policy import POLICY_FILE
from tradingagents.web.workers.checkpoints import backup_database

_MAX_TOTAL = 2_000_000_000
_MAX_ENTRIES = 20_000
_PATH_COLUMNS = (("reports", "file_path"), ("data_snapshots", "file_path"))


def _digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _connect(path):
    return closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))


def _quiet(connection):
    active = connection.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
    if active:
        raise ValueError("Stop or finish queued/running research before a recovery bundle; no tasks were changed")


def _inventory(connection):
    return {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in ("runs", "reports", "data_snapshots", "watchlists", "research_viewpoints",
                "investment_policies", "investment_policy_versions", "investment_plans")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()}


def _artifact(root, value):
    path = Path(value)
    candidate = path if path.is_absolute() else root / "research" / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root):
        raise ValueError("Referenced artifact is outside the state root or uses a symlink")
    if not candidate.is_file():
        raise ValueError("Referenced artifact is missing; refusing an incomplete bundle")
    return candidate


def create_bundle(state_root, output):
    root, output = Path(state_root).resolve(), Path(output).absolute()
    database = root / "workbench.sqlite3"
    if output.exists() or output.is_symlink():
        raise FileExistsError("Bundle destination already exists")
    if not database.is_file() or database.is_symlink():
        raise ValueError("State database is missing or unsafe")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="tradingagents-backup-", dir=output.parent) as temporary:
        staging = Path(temporary)
        with _connect(database) as source:
            _quiet(source)
            before = _inventory(source)
            revision = source.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        backup_database(database, staging / "workbench.sqlite3")
        # A standalone archive must not depend on unbundled WAL/SHM sidecars.
        with closing(sqlite3.connect(staging / "workbench.sqlite3")) as standalone:
            standalone.execute("PRAGMA journal_mode=DELETE")
        paths = {"workbench.sqlite3": staging / "workbench.sqlite3"}
        with _connect(staging / "workbench.sqlite3") as frozen:
            _quiet(frozen)
            for table, column in _PATH_COLUMNS:
                for (value,) in frozen.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                    source_path = _artifact(root, value)
                    paths[source_path.relative_to(root).as_posix()] = source_path
            for (reference,) in frozen.execute("SELECT checkpoint_ref FROM run_attempts WHERE checkpoint_ref IS NOT NULL"):
                metadata_path = _artifact(root, reference)
                metadata = json.loads(metadata_path.read_text())
                name = metadata.get("database_file")
                if not isinstance(name, str) or Path(name).name != name or not name.endswith(".db"):
                    raise ValueError("Invalid checkpoint metadata")
                checkpoint = _artifact(root, str(metadata_path.with_name(name)))
                if _digest(checkpoint) != metadata.get("database_sha256"):
                    raise ValueError("Checkpoint hash failed")
                for item in (metadata_path, checkpoint):
                    paths[item.relative_to(root).as_posix()] = item
        if len(paths) > _MAX_ENTRIES or sum(path.stat().st_size for path in paths.values()) > _MAX_TOTAL:
            raise ValueError("Bundle exceeds recovery size limits")
        hashes = {name: _digest(path) for name, path in paths.items()}
        manifest = {"version": 1, "created_at": datetime.now(UTC).isoformat(), "schema_revision": revision,
                    "source_root": str(root), "inventory": before, "files": hashes,
                    "credentials": "provider secrets excluded; all connections disabled and require rebinding on restore",
                    "accounts": "private account password hashes included; sessions revoked on restore",
                    "scope": "private local recovery; does not grant data redistribution or multi-user access"}
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("bundle.json", json.dumps(manifest, sort_keys=True))
                for name, path in sorted(paths.items()):
                    archive.write(path, name)
                    if _digest(path) != hashes[name]:
                        raise ValueError("Artifact changed during backup; retry with services stopped")
            with _connect(database) as source:
                _quiet(source)
                if _inventory(source) != before:
                    raise ValueError("State changed during backup; retry with services stopped")
            verify_bundle(output)
            return {"schema_revision": revision, "files": len(paths), "inventory": before, "credentials_included": False}
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def verify_bundle(bundle):
    with zipfile.ZipFile(bundle) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) > _MAX_ENTRIES + 1 or len(names) != len(set(names)):
            raise ValueError("Duplicate or excessive bundle entries")
        if sum(item.file_size for item in entries) > _MAX_TOTAL:
            raise ValueError("Bundle exceeds recovery size limits")
        for item in entries:
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or item.is_dir()
                    or (mode & 0o170000) == 0o120000):
                raise ValueError("Unsafe bundle entry")
        metadata_entry = archive.getinfo("bundle.json")
        if metadata_entry.file_size > 5_000_000:
            raise ValueError("Bundle manifest exceeds limit")
        manifest = json.loads(archive.read("bundle.json"))
        if manifest.get("version") != 1 or set(manifest["files"]) | {"bundle.json"} != set(names):
            raise ValueError("Invalid bundle inventory")
        for name, expected in manifest["files"].items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("Bundle content hash failed")
        if "workbench.sqlite3" not in manifest["files"]:
            raise ValueError("Bundle database is missing")
        return manifest


def restore_bundle(bundle, destination):
    """Only restore into a new directory. No server or task is started."""
    bundle, destination = Path(bundle).resolve(), Path(destination).absolute()
    manifest = verify_bundle(bundle)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Restore destination must be a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="tradingagents-restore-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "state"
        staging.mkdir(mode=0o700)
        with zipfile.ZipFile(bundle) as archive:
            for name in manifest["files"]:
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(name) as source, path.open("xb") as target:
                    path.chmod(0o600)
                    shutil.copyfileobj(source, target)
                if _digest(path) != manifest["files"][name]:
                    raise ValueError("Bundle changed during restoration")
        with closing(sqlite3.connect(staging / "workbench.sqlite3")) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Restored database integrity failed")
            old_root = Path(manifest["source_root"])
            for table, column in _PATH_COLUMNS:
                rows = connection.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall()
                for rowid, original in rows:
                    path = Path(original)
                    relative = path.relative_to(old_root) if path.is_absolute() else Path("research") / path
                    if relative.as_posix() not in manifest["files"]:
                        raise ValueError("Referenced artifact absent from bundle")
                    connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (str(destination / relative), rowid))
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            reset = {
                "model_connections": "credential_ref=NULL,extra_header_ref=NULL,enabled=0,revision=revision+1",
                "model_profiles": "test_status='untested',tested_at=NULL,config_hash=NULL",
                "research_data_connections": "credential_ref=NULL,user_agent_ref=NULL,credential_source='keychain',enabled=0,test_status='untested',tested_at=NULL,revision=revision+1",
                "market_data_connections": "credential_ref='rebind-required',secondary_credential_ref=NULL,enabled=0,test_results='{}',tested_at=NULL,revision=revision+1",
                "agent_runtime_connections": "enabled=0,readiness='stale',probe_result='{}',checked_at=NULL,revision=revision+1",
            }
            for table, assignments in reset.items():
                if table in tables:
                    connection.execute(f'UPDATE "{table}" SET {assignments}')
            if "worker_heartbeats" in tables:
                connection.execute("DELETE FROM worker_heartbeats")
            # Recovery must never resurrect a previously valid browser session
            # or copy an old single-use owner-setup capability into a new store.
            if "auth_sessions" in tables:
                connection.execute("DELETE FROM auth_sessions")
            if "auth_rate_limits" in tables:
                connection.execute("DELETE FROM auth_rate_limits")
            if "auth_settings" in tables:
                connection.execute("UPDATE auth_settings SET setup_token_digest=NULL,setup_created_at=NULL")
            _quiet(connection)
            restored = _inventory(connection)
            if restored != manifest["inventory"]:
                raise ValueError("Restored inventory differs from bundle")
            connection.commit()
        policy = staging / POLICY_FILE
        policy.write_text(json.dumps({"version": 1, "legacy_local_auth_disabled": True,
            "reason": "Configure and test an explicit model connection/template after restore; ambient local accounts are not inherited."}))
        policy.chmod(0o600)
        # The final destination has never existed; this does not overwrite live data.
        staging.rename(destination)
    return {"schema_revision": manifest["schema_revision"], "inventory": restored,
            "credentials_rebind_required": True, "services_started": False}
