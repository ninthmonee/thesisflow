"""A recovery drill uses a new directory, restores artifacts and requires fresh credentials."""

import hashlib
import sqlite3
import zipfile

import pytest

from tradingagents.web.application.research import ResearchService
from tradingagents.web.infrastructure.backup_bundle import (
    create_bundle,
    restore_bundle,
    verify_bundle,
)
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import ModelConnection, Report


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "state"
    database = initialize_database(root / "workbench.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, root / "research")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    run = service.create_run({"ticker": "NVDA", "date": "2024-01-02"}, **scope)
    service.tasks.finish(service.tasks.claim_next("test"), "completed")
    report = root / "research" / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Saved report")
    (root / ".env").write_text("DO-NOT-READ-OR-PACKAGE")
    with database.session() as session:
        session.add(ModelConnection(id="connection", **scope, name="saved", provider="openai", credential_ref="opaque-ref"))
        session.add(Report(workspace_id=local.workspace_id, run_id=run["id"], kind="_export_markdown",
            file_path="report.md", content_hash=hashlib.sha256(report.read_bytes()).hexdigest()))
    yield root, database, service, scope
    database.dispose()


def test_bundle_restore_drill_preserves_data_but_disables_account_reuse(state, tmp_path):
    root, database, _service, _scope = state
    bundle = tmp_path / "recovery.zip"
    result = create_bundle(root, bundle)
    assert result["credentials_included"] is False and bundle.stat().st_mode & 0o777 == 0o600
    metadata = verify_bundle(bundle)
    assert metadata["schema_revision"] == "0010_research_viewpoints"
    with zipfile.ZipFile(bundle) as archive:
        assert ".env" not in archive.namelist()
        assert all(b"DO-NOT-READ-OR-PACKAGE" not in archive.read(name) for name in archive.namelist())
    destination = tmp_path / "restored"
    restored = restore_bundle(bundle, destination)
    assert restored["credentials_rebind_required"] and not restored["services_started"]
    assert (destination / "research" / "report.md").read_text() == "Saved report"
    with sqlite3.connect(destination / "workbench.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT enabled,credential_ref FROM model_connections").fetchone() == (0, None)
        assert connection.execute("SELECT file_path FROM reports").fetchone()[0] == str(destination / "research" / "report.md")
    with database.engine.connect() as connection:
        from sqlalchemy import text
        assert connection.execute(text("SELECT enabled,credential_ref FROM model_connections")).one() == (1, "opaque-ref")
    with pytest.raises(FileExistsError):
        restore_bundle(bundle, destination)


def test_backup_refuses_pending_research_and_incomplete_references(state, tmp_path):
    root, _database, service, scope = state
    service.create_run({"ticker": "NVDA", "date": "2024-01-03"}, **scope)
    with pytest.raises(ValueError, match="queued/running"):
        create_bundle(root, tmp_path / "pending.zip")
    assert not (tmp_path / "pending.zip").exists()


def test_verify_rejects_hash_changes_and_path_traversal(state, tmp_path):
    root, _, _, _ = state
    original = tmp_path / "good.zip"
    create_bundle(root, original)
    corrupt = tmp_path / "bad.zip"
    with zipfile.ZipFile(original) as reader, zipfile.ZipFile(corrupt, "w") as writer:
        for name in reader.namelist():
            writer.writestr(name, b"bad" if name.endswith("report.md") else reader.read(name))
    with pytest.raises(ValueError, match="hash"):
        restore_bundle(corrupt, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()
    with zipfile.ZipFile(corrupt, "a") as writer:
        writer.writestr("../escape", "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        verify_bundle(corrupt)
