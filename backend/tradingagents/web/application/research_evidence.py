"""Fenced, private evidence artifacts and owner-scoped independent qualification."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from tradingagents.dataflows.credentials import configured_credentials
from tradingagents.dataflows.evidence import EvidenceCaptureError
from tradingagents.web.api.research_evidence_schemas import (
    ResearchEvidenceItem,
    ResearchEvidencePage,
    ResearchQualitySummary,
)
from tradingagents.web.application.research_repository import TaskRepository
from tradingagents.web.domain.research_quality import (
    MAX_BODY_BYTES,
    issue,
    normalize_observation,
    qualify,
)
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.models import (
    DataSnapshot,
    Evidence,
    Membership,
    Report,
    RunManifest,
)


class ResearchEvidenceStore:
    MAX_RECORDS = 500

    def __init__(self, database, artifacts_root, *, clock=None):
        self.database = database
        self.root = Path(artifacts_root).expanduser().resolve()
        self.tasks = TaskRepository(database, clock)

    def _scope(self, session, run_id, workspace_id, owner_id):
        membership = session.scalar(
            select(Membership.id).where(
                Membership.workspace_id == workspace_id, Membership.user_id == owner_id
            )
        )
        if membership is None:
            raise ResearchError("research_evidence_forbidden", "无权访问此研究证据", 403)
        return self.tasks.scoped_run(session, run_id, workspace_id, owner_id)

    def begin(self, claim, manifest):
        with self.tasks.transaction() as session:
            self.tasks._fenced(session, claim)
            session.add(
                Evidence(
                    id=str(uuid4()),
                    workspace_id=claim.workspace_id,
                    run_id=claim.run_id,
                    kind="capture_session",
                    label="工具证据捕获已开始",
                    locator={
                        "capture_version": "research-evidence-v1",
                        "attempt_id": claim.attempt_id,
                    },
                )
            )
        return EvidenceCaptureSession(
            self, claim, manifest["request"]["ticker"], manifest["request"]["date"]
        )

    def _folder(self, claim):
        folder = self.root
        for part in (claim.workspace_id, claim.run_id, "evidence"):
            if part != "evidence" and str(UUID(part)) != part:
                raise ResearchError("invalid_evidence_path", "研究证据路径无效", 503)
            folder = folder / part
            if folder.is_symlink():
                raise ResearchError("invalid_evidence_path", "研究证据路径无效", 503)
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)
        return folder

    def _write(self, claim, identifier, body):
        folder = self._folder(claim)
        path = folder / f"{identifier}.json"
        encoded = json.dumps({"body": body}, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_BODY_BYTES + 1000:
            raise ResearchError("evidence_size_exceeded", "证据摘录超过保存上限", 503)
        handle, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path, hashlib.sha256(encoded).hexdigest(), len(encoded)

    def record(self, claim, details, body):
        path = None
        try:
            with self.tasks.transaction() as session:
                self.tasks._fenced(session, claim)
                count = len(
                    session.scalars(
                        select(Evidence.id).where(
                            Evidence.workspace_id == claim.workspace_id,
                            Evidence.run_id == claim.run_id,
                            Evidence.kind == "tool_response",
                        )
                    ).all()
                )
                if count >= self.MAX_RECORDS:
                    raise ResearchError(
                        "evidence_record_limit", "研究工具证据已达500条上限；请缩小研究范围", 409
                    )
                identifier, now = str(uuid4()), self.tasks.now()
                path, digest, size = self._write(claim, identifier, body)
                metadata = {
                    **details,
                    "attempt_id": claim.attempt_id,
                    "stored_bytes": size,
                    "capture_version": "research-evidence-v1",
                }
                snapshot = DataSnapshot(
                    id=identifier,
                    workspace_id=claim.workspace_id,
                    source=details["provider"],
                    kind=details["category"],
                    file_path=str(path.relative_to(self.root)),
                    content_hash=digest,
                    # Unknown source instants remain null in details/public DTO.
                    # Required DB as_of is the capture instant, explicitly labelled here.
                    as_of=now,
                    received_at=now,
                    published_at=None,
                    details={**metadata, "db_as_of_kind": "capture_instant_not_source_time"},
                )
                session.add(snapshot)
                session.flush()
                session.add(
                    Evidence(
                        id=str(uuid4()),
                        workspace_id=claim.workspace_id,
                        run_id=claim.run_id,
                        snapshot_id=identifier,
                        kind="tool_response",
                        label=details["method"],
                        locator={"attempt_id": claim.attempt_id},
                    )
                )
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        return identifier

    def _read(self, snapshot):
        path = self.root / snapshot.file_path
        if (
            path.resolve() != path
            or not path.is_relative_to(self.root)
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_BODY_BYTES + 1000
        ):
            raise ValueError("invalid evidence artifact")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != snapshot.content_hash:
            raise ValueError("evidence integrity mismatch")
        body = json.loads(raw)["body"]
        if body is not None and (not isinstance(body, str) or len(body.encode()) > MAX_BODY_BYTES):
            raise ValueError("invalid evidence body")
        return body

    def _records(self, session, run_id, workspace_id):
        return session.execute(
            select(Evidence, DataSnapshot)
            .outerjoin(
                DataSnapshot,
                (Evidence.snapshot_id == DataSnapshot.id)
                & (Evidence.workspace_id == DataSnapshot.workspace_id),
            )
            .where(
                Evidence.workspace_id == workspace_id,
                Evidence.run_id == run_id,
                Evidence.kind == "tool_response",
            )
            .order_by(Evidence.created_at, Evidence.id)
        ).all()

    def _quality(self, session, run, rows):
        started = (
            session.scalar(
                select(Evidence.id)
                .where(
                    Evidence.workspace_id == run.workspace_id,
                    Evidence.run_id == run.id,
                    Evidence.kind == "capture_session",
                )
                .limit(1)
            )
            is not None
        )
        manifest = session.get(RunManifest, run.id)
        analysts = manifest.content.get("request", {}).get("analysts", []) if manifest else []
        details, problems = [], []
        if run.error_code == "research_evidence_capture_failed":
            problems.append(issue("capture_failed", "本次证据捕获曾失败，不能宣称证据包完整"))
        for _evidence, snapshot in rows:
            if snapshot is None:
                problems.append(issue("evidence_snapshot_missing", "研究证据快照缺失"))
                continue
            item = dict(snapshot.details)
            try:
                self._read(snapshot)
            except (OSError, ValueError, KeyError, TypeError):
                item.update(status="unknown", price_validated=False)
                problems.append(
                    issue(
                        "evidence_integrity_failed", "证据文件缺失或完整性校验失败；结论资格已撤回"
                    )
                )
            details.append(item)
        if problems:
            details = [{**item, "price_validated": False} for item in details]
        checked = max((snapshot.received_at for _, snapshot in rows if snapshot), default=None)
        selection = manifest.content.get("selection", {}) if manifest else {}
        external = selection.get("execution_mode") == "local_agent"
        required = None
        if external:
            from tradingagents.web.domain.agent_research import evidence_policy

            policy = evidence_policy(manifest.content.get("request", {}).get("objective", "general"))
            required = policy["required_categories"]
            if selection.get("evidence_policy") != policy:
                started = False
                problems.append(issue("runtime_evidence_policy_invalid", "外部研究的证据策略缺失或不兼容，不能推导结论资格"))
        result = qualify(
                details,
                analysts,
                capture_started=started,
                checked_at=checked,
                capture_issues=problems,
                required_categories=required,
            )
        if external:
            result.update(rule_version="local-agent-evidence-v1", eligible_for_rating=False)
            result["issues"].append(issue("runtime_claims_unverified", "本地Agent报告保留为原始研究观点；引用存在性不等于报告中的结论、目标与仓位已被独立核验"))
        return started, ResearchQualitySummary.model_validate(result)

    def get_quality(self, run_id, *, workspace_id, owner_id):
        with self.database.session() as session:
            run = self._scope(session, run_id, workspace_id, owner_id)
            return self._quality(session, run, self._records(session, run_id, workspace_id))[1]

    def list_evidence(
        self, run_id, *, workspace_id, owner_id, limit=25, cursor=None, include_body=False
    ):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ResearchError("invalid_evidence_limit", "证据分页数量须为1–100", 422)
        if cursor is not None:
            try:
                cursor = str(UUID(cursor))
            except (ValueError, TypeError):
                raise ResearchError("invalid_evidence_cursor", "证据分页游标无效", 422) from None
        with self.database.session() as session:
            run = self._scope(session, run_id, workspace_id, owner_id)
            rows = self._records(session, run_id, workspace_id)
            started, quality = self._quality(session, run, rows)
            if cursor:
                positions = [index for index, (row, _) in enumerate(rows) if row.id == cursor]
                if not positions:
                    raise ResearchError("invalid_evidence_cursor", "证据分页游标不属于此研究", 422)
                rows = rows[positions[0] + 1 :]
            items = []
            # Bound a page with bodies to at most five 48 KB excerpts.
            page_size = min(limit, 5) if include_body else limit
            for evidence, snapshot in rows[:page_size]:
                if snapshot is None:
                    continue
                details, body, problems = dict(snapshot.details), None, []
                try:
                    safe_body = self._read(snapshot)
                    body = safe_body if include_body else None
                except (OSError, ValueError, KeyError, TypeError):
                    details["status"] = "unknown"
                    problems.append(
                        issue("evidence_integrity_failed", "证据文件缺失或完整性校验失败")
                    )
                items.append(
                    ResearchEvidenceItem(
                        id=evidence.id,
                        snapshot_id=snapshot.id,
                        attempt_id=details.get("attempt_id"),
                        method=details["method"],
                        category=details["category"],
                        provider=details["provider"],
                        status=details["status"],
                        request=details["request"],
                        requested_as_of=details.get("requested_as_of"),
                        data_as_of=details.get("data_as_of"),
                        published_at=snapshot.published_at,
                        received_at=snapshot.received_at,
                        response_hash=details.get("response_hash"),
                        stored_bytes=details.get("stored_bytes", 0),
                        truncated=bool(details.get("truncated")),
                        body=body,
                        issues=[*details.get("issues", []), *problems],
                    )
                )
            next_cursor = rows[page_size - 1][0].id if len(rows) > page_size else None
            return ResearchEvidencePage(
                run_id=run_id,
                items=items,
                next_cursor=next_cursor,
                capture_started=started,
                quality=quality,
            )

    def finalize(self, claim):
        quality = self.get_quality(
            claim.run_id, workspace_id=claim.workspace_id, owner_id=claim.owner_id
        )
        content = quality.model_dump_json()
        with self.tasks.transaction() as session:
            run, _ = self.tasks._fenced(session, claim)
            previous = session.scalars(
                select(Report)
                .where(Report.run_id == claim.run_id, Report.kind == "_research_quality")
                .order_by(Report.version.desc())
                .limit(1)
            ).first()
            session.add(
                Report(
                    workspace_id=claim.workspace_id,
                    run_id=claim.run_id,
                    kind="_research_quality",
                    version=previous.version + 1 if previous else 1,
                    content=content,
                    structured=quality.model_dump(mode="json"),
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
            run.data_quality = (
                "complete"
                if quality.status == "usable"
                else "unavailable"
                if quality.status in {"insufficient", "unverified"}
                else "partial"
            )
        return quality


class EvidenceCaptureSession:
    def __init__(self, store, claim, symbol, analysis_date):
        self.store, self.claim = store, claim
        self.symbol, self.analysis_date = symbol, analysis_date
        self.failure = False
        self.lock = threading.Lock()
        self.captured_ids = []
        self.observations = []

    def verify(self):
        if self.failure:
            raise ResearchError(
                "research_evidence_capture_failed",
                "工具证据捕获未完成；研究保留已有报告但不发布有效结论",
                503,
            )

    def __call__(self, method, provider, args, kwargs, response, outcome, error_type):
        with self.lock:
            self.verify()
            try:
                credentials = (
                    configured_credentials(provider)
                    if provider in {"alpha_vantage", "fred", "sec_edgar"}
                    else {}
                )
                redactions = [
                    value
                    for key, value in (credentials or {}).items()
                    if key in {"api_key", "user_agent"} and isinstance(value, str)
                ]
                details, body = normalize_observation(
                    method,
                    provider,
                    args,
                    kwargs,
                    response,
                    outcome,
                    error_type,
                    symbol=self.symbol,
                    analysis_date=self.analysis_date,
                    received_at=self.store.tasks.now(),
                    redactions=redactions,
                )
                identifier = self.store.record(self.claim, details, body)
                if isinstance(identifier, str):
                    self.captured_ids.append(identifier)
                    self.observations.append({"id": identifier, "details": details, "body": body})
            except Exception:
                self.failure = True
                raise EvidenceCaptureError("Research evidence capture failed.") from None
