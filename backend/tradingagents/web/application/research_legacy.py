"""Explicit read-only import of legacy JSON runs; imported tasks never execute."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from tradingagents.report_html import render_report
from tradingagents.reporting import write_report_tree
from tradingagents.web.api.schemas import CreateResearchRequest
from tradingagents.web.infrastructure.models import LegacyImport, Report, ResearchRun, RunManifest

from .research import REPORT_LABELS, ResearchService, content_hash


def _read_file(path: Path, root: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("unsafe source file")
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("unsafe source directory")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("source file exceeds import limit")
    return data


def _date(value: Any, fallback: datetime | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return fallback
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return fallback


def import_legacy_runs(
    service: ResearchService,
    source_root: str | Path,
    *,
    workspace_id: str,
    owner_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Import validated records and safe report renderings, preserving every source byte."""
    source = Path(source_root).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("legacy source must be an existing directory, not a symlink")
    source = source.resolve()
    root_hash = hashlib.sha256(str(source).encode()).hexdigest()
    scope = {"workspace_id": workspace_id, "owner_id": owner_id}
    with service.database.session() as session:
        service._scope(session, workspace_id, owner_id, write=True)
    summary = {
        "dry_run": dry_run,
        "imported": 0,
        "already_imported": 0,
        "skipped": 0,
        "records": [],
    }
    for path in sorted(source.glob("*/run.json")):
        legacy_id = path.parent.name
        try:
            if str(UUID(legacy_id)) != legacy_id:
                raise ValueError("invalid record id")
            raw = _read_file(path, source, 5 * 1024 * 1024)
            saved = json.loads(raw)
            if not isinstance(saved, dict) or saved.get("id") != legacy_id:
                raise ValueError("mismatched record id")
            stored_reports = saved.get("reports") or {}
            if not isinstance(stored_reports, dict):
                raise ValueError("invalid report map")
            body = CreateResearchRequest.model_validate(
                {
                    "ticker": saved.get("ticker"),
                    "date": saved.get("date"),
                    "analysts": saved.get("analysts")
                    or ["market", "social", "news", "fundamentals"],
                    "depth": saved.get("depth", 1),
                    "language": saved.get("language", "Chinese"),
                    "checkpoint": False,
                }
            ).model_dump()
            exports = {}
            for filename in ("complete_report.md", "complete_report.html"):
                candidate = path.parent / "report" / filename
                if candidate.exists():
                    exports[filename] = _read_file(candidate, source, 10 * 1024 * 1024)
            digest = content_hash(
                {
                    "record": hashlib.sha256(raw).hexdigest(),
                    "files": {
                        key: hashlib.sha256(value).hexdigest() for key, value in exports.items()
                    },
                }
            )
        except (OSError, ValueError, TypeError):
            summary["skipped"] += 1
            summary["records"].append(
                {
                    "legacy_run_id": legacy_id,
                    "status": "skipped",
                    "reason": "记录、路径或报告未通过校验",
                }
            )
            continue
        with service.database.session() as session:
            previous = session.scalar(
                select(LegacyImport).where(
                    LegacyImport.workspace_id == workspace_id,
                    LegacyImport.source_root_hash == root_hash,
                    LegacyImport.legacy_run_id == legacy_id,
                    LegacyImport.source_hash == digest,
                )
            )
            run_id = str(
                uuid5(NAMESPACE_URL, f"tradingagents:legacy:{workspace_id}:{root_hash}:{legacy_id}")
            )
            existing_run = session.get(ResearchRun, run_id)
            if previous is not None:
                summary["already_imported"] += 1
                summary["records"].append(
                    {
                        "legacy_run_id": legacy_id,
                        "run_id": previous.run_id,
                        "status": "already_imported",
                    }
                )
                continue
            if existing_run is not None:
                summary["skipped"] += 1
                summary["records"].append(
                    {
                        "legacy_run_id": legacy_id,
                        "run_id": run_id,
                        "status": "skipped",
                        "reason": "来源记录已改变，保留已导入快照",
                    }
                )
                continue
        if dry_run:
            summary["imported"] += 1
            summary["records"].append(
                {"legacy_run_id": legacy_id, "run_id": run_id, "status": "would_import"}
            )
            continue
        warnings = ["历史模型、数据来源和配置清单不完整；未推断缺失信息"]
        status = saved.get("status")
        if not isinstance(status, str):
            status = "interrupted"
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            status = "interrupted"
            warnings.append("历史活跃或未知任务已标记中断，不会自动执行")
        created = _date(saved.get("created_at"), service.tasks.now())
        if _date(saved.get("created_at"), None) is None:
            warnings.append("来源创建时间缺失，使用导入时间")
        final_reports = {
            key: value
            for key, value in stored_reports.items()
            if key in REPORT_LABELS and isinstance(value, str)
        }
        mapping = {}
        if final_reports or "complete_report.md" in exports:
            output = service.run_directory(workspace_id, run_id) / "legacy" / str(uuid4())
            output.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Regenerate HTML through the escaping renderer; never serve imported HTML verbatim.
            write_report_tree(
                final_reports, body["ticker"], output, settings={"version": "legacy"}, html=True
            )
            if "complete_report.md" in exports:
                markdown = exports["complete_report.md"].decode("utf-8", errors="replace")
                (output / "complete_report.md").write_text(markdown, encoding="utf-8")
                parts = [("历史完整报告", "legacy", [("历史报告", "complete_report.md", markdown)])]
                (output / "complete_report.html").write_text(
                    render_report(body["ticker"], final_reports, {"version": "legacy"}, parts),
                    encoding="utf-8",
                )
            for kind, filename in (
                ("html", "complete_report.html"),
                ("markdown", "complete_report.md"),
            ):
                destination = output / filename
                destination.chmod(0o600)
                mapping[kind] = str(destination.relative_to(service.artifacts_root))
        elif "complete_report.html" in exports:
            warnings.append("来源仅有原始 HTML，未直接展示不可信 HTML；原文件保留")
        document = {
            "schema_version": "1",
            "graph_contract_version": "legacy",
            **scope,
            "request": body,
            "asset_type": "crypto" if saved.get("asset_type") == "crypto" else "stock",
            "runtime": {},
            "selection": {"legacy": {"configuration_status": "unknown", "warnings": warnings}},
            "memory_scope": "legacy-read-only",
        }
        with service.tasks.transaction() as session:
            service._scope(session, workspace_id, owner_id, write=True)
            if session.get(ResearchRun, run_id) is not None:
                summary["already_imported"] += 1
                continue
            run = ResearchRun(
                id=run_id,
                **scope,
                ticker=body["ticker"],
                analysis_date=datetime.fromisoformat(body["date"]).date(),
                status=status,
                stage="completed" if status == "completed" else "initializing",
                data_quality="unavailable",
                request_hash=digest,
                created_at=created,
                started_at=_date(saved.get("started_at"), None),
                finished_at=_date(saved.get("finished_at"), None),
                error_code="legacy_incomplete" if status in {"failed", "interrupted"} else None,
                error_message="历史任务未完成，已保留可用阶段报告"
                if status in {"failed", "interrupted"}
                else None,
            )
            session.add(run)
            session.flush()
            session.add(
                RunManifest(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    schema_version="1",
                    content=document,
                    content_hash=content_hash(document),
                )
            )
            for kind, report_content in final_reports.items():
                session.add(
                    Report(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        kind=kind,
                        version=1,
                        content=report_content,
                        content_hash=hashlib.sha256(report_content.encode()).hexdigest(),
                    )
                )
            if isinstance(saved.get("rating"), str) and saved["rating"] in {
                "Buy",
                "Overweight",
                "Hold",
                "Underweight",
                "Sell",
                "REVIEW",
            }:
                rating = saved["rating"]
                session.add(
                    Report(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        kind="_rating",
                        version=1,
                        content=rating,
                        content_hash=hashlib.sha256(rating.encode()).hexdigest(),
                    )
                )
            for kind, relative in mapping.items():
                content = (service.artifacts_root / relative).read_bytes()
                session.add(
                    Report(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        kind=f"_export_{kind}",
                        version=1,
                        file_path=relative,
                        content_hash=hashlib.sha256(content).hexdigest(),
                    )
                )
            session.add(
                LegacyImport(
                    **scope,
                    source_root_hash=root_hash,
                    legacy_run_id=legacy_id,
                    source_hash=digest,
                    run_id=run_id,
                    status="imported",
                    warnings=warnings,
                    file_mapping=mapping,
                )
            )
            service.tasks.append_event(
                session, run, "state", {"status": status, "stage": run.stage}
            )
        summary["imported"] += 1
        summary["records"].append(
            {"legacy_run_id": legacy_id, "run_id": run_id, "status": "imported"}
        )
    return summary


def main() -> None:
    import argparse

    from tradingagents.web.infrastructure.database import initialize_database

    parser = argparse.ArgumentParser(
        description="Validate/import legacy Web records without changing source files"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="import; default only validates and lists candidates"
    )
    args = parser.parse_args()
    database = initialize_database(args.database)
    try:
        local = database.local_workspace()
        summary = import_legacy_runs(
            ResearchService(database, args.artifacts),
            args.source,
            workspace_id=local.workspace_id,
            owner_id=local.user_id,
            dry_run=not args.apply,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
