"""Legacy import never mutates source records or executes old active tasks."""

import hashlib
import json
from uuid import uuid4

import pytest

from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_legacy import import_legacy_runs
from tradingagents.web.infrastructure.database import initialize_database


@pytest.fixture
def legacy(tmp_path):
    database = initialize_database(tmp_path / "app.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, tmp_path / "artifacts")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    source = tmp_path / "old-web"
    source.mkdir()
    yield service, scope, source
    database.dispose()


def write_legacy(source, *, status="completed", reports=None):
    run_id = str(uuid4())
    folder = source / run_id
    folder.mkdir()
    saved = {
        "id": run_id,
        "ticker": "NVDA",
        "date": "2024-01-02",
        "analysts": ["market"],
        "depth": 1,
        "language": "Chinese",
        "status": status,
        "asset_type": "stock",
        "created_at": "2024-01-02T10:00:00+00:00",
        "rating": "Buy",
        "error": "private upstream error sk-secret",
        "reports": reports or {"market_report": "historical evidence"},
    }
    path = folder / "run.json"
    path.write_text(json.dumps(saved))
    return path


def test_dry_run_does_not_write_database_or_artifacts(legacy):
    service, scope, source = legacy
    write_legacy(source)
    result = import_legacy_runs(service, source, **scope)
    assert result["dry_run"] is True and result["imported"] == 1
    assert service.list_runs(**scope)["items"] == []
    assert not service.artifacts_root.exists()


def test_import_is_idempotent_and_source_bytes_unchanged(legacy):
    service, scope, source = legacy
    path = write_legacy(source)
    original = path.read_bytes()
    first = import_legacy_runs(service, source, dry_run=False, **scope)
    second = import_legacy_runs(service, source, dry_run=False, **scope)
    assert first["imported"] == 1
    assert second["imported"] == 0 and second["already_imported"] == 1
    assert path.read_bytes() == original
    run_id = first["records"][0]["run_id"]
    run = service.get_run(run_id, **scope)
    assert run["status"] == "completed" and run["rating"] is None
    assert run["raw_rating"] == "Buy" and run["quality"]["status"] == "unverified"
    assert run["reports"] == {"market_report": "historical evidence"}
    assert "sk-secret" not in json.dumps(run)
    assert (
        service.get_manifest(run_id, **scope)["selection"]["legacy"]["configuration_status"]
        == "unknown"
    )


def test_active_legacy_tasks_are_not_queued_or_executed(legacy):
    service, scope, source = legacy
    write_legacy(source, status="running")
    result = import_legacy_runs(service, source, dry_run=False, **scope)
    run_id = result["records"][0]["run_id"]
    assert service.get_run(run_id, **scope)["status"] == "interrupted"
    assert service.tasks.claim_next("worker") is None


def test_imported_html_is_regenerated_with_escaped_content(legacy):
    service, scope, source = legacy
    path = write_legacy(source)
    folder = path.parent / "report"
    folder.mkdir()
    malicious = '<script>alert("private")</script>'
    (folder / "complete_report.html").write_text(malicious)
    (folder / "complete_report.md").write_text("# Historical\n" + malicious)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    result = import_legacy_runs(service, source, dry_run=False, **scope)
    exported = service.report_file(result["records"][0]["run_id"], **scope).read_text()
    assert malicious not in exported
    assert "&lt;script&gt;" in exported
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def test_unsafe_or_corrupt_records_are_skipped_without_stopping_import(legacy, tmp_path):
    service, scope, source = legacy
    write_legacy(source)
    bad_folder = source / str(uuid4())
    bad_folder.mkdir()
    (bad_folder / "run.json").write_text("not-json")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_path = write_legacy(outside)
    (source / outside_path.parent.name).symlink_to(outside_path.parent, target_is_directory=True)
    result = import_legacy_runs(service, source, dry_run=False, **scope)
    assert result["imported"] == 1
    assert result["skipped"] == 2
    assert len(service.list_runs(**scope)["items"]) == 1


def test_changed_legacy_record_does_not_overwrite_imported_snapshot(legacy):
    service, scope, source = legacy
    path = write_legacy(source)
    first = import_legacy_runs(service, source, dry_run=False, **scope)
    saved = json.loads(path.read_text())
    saved["reports"]["market_report"] = "changed after import"
    path.write_text(json.dumps(saved))
    second = import_legacy_runs(service, source, dry_run=False, **scope)
    assert second["skipped"] == 1
    run = service.get_run(first["records"][0]["run_id"], **scope)
    assert run["reports"]["market_report"] == "historical evidence"
