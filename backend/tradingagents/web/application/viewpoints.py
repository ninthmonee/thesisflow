"""Human follow-up and deterministic same-instrument report comparison."""

import json
from datetime import UTC, datetime
from difflib import unified_diff
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tradingagents.web.api.viewpoint_schemas import ResearchComparison, ViewpointView
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.models import DataSnapshot, Evidence, Membership
from tradingagents.web.infrastructure.viewpoint_model import ResearchViewpoint


class ViewpointService:
    def __init__(self, database, workspace, research, *, clock=None):
        self.database, self.workspace, self.research = database, workspace, research
        self.clock = clock or (lambda: datetime.now(UTC))

    @property
    def scope(self):
        return {"workspace_id": self.workspace.workspace_id, "owner_id": self.workspace.user_id}

    def _authorize(self, session, write=False):
        role = session.scalar(select(Membership.role).where(
            Membership.workspace_id == self.workspace.workspace_id, Membership.user_id == self.workspace.user_id))
        if role is None:
            raise ResearchError("workspace_not_found", "工作区不存在", 404)
        if write and role not in {"owner", "researcher"}:
            raise ResearchError("viewpoint_forbidden", "当前权限不能修改观点", 403)

    def _row(self, session, viewpoint_id):
        row = session.scalar(select(ResearchViewpoint).where(
            ResearchViewpoint.id == viewpoint_id, ResearchViewpoint.workspace_id == self.workspace.workspace_id,
            ResearchViewpoint.owner_id == self.workspace.user_id))
        if row is None:
            raise ResearchError("viewpoint_not_found", "观点不存在", 404)
        return row

    def _view(self, row):
        run = self.research.get_run(row.run_id, **self.scope)
        return ViewpointView(
            id=row.id, run_id=row.run_id, ticker=row.ticker, stance=row.stance, thesis=row.thesis,
            invalidation=row.invalidation, review_on=row.review_on, review_notes=row.review_notes,
            status=row.status, revision=row.revision,
            review_due=bool(row.status == "active" and row.review_on and row.review_on <= self.clock().astimezone(ZoneInfo("America/New_York")).date().isoformat()),
            confirmed_at=row.confirmed_at, reviewed_at=row.reviewed_at, created_at=row.created_at,
            updated_at=row.updated_at, source_quality=run["quality"]["status"],
        )

    def list(self, *, ticker=None, run_id=None, include_archived=False):
        with self.database.session() as session:
            self._authorize(session)
            query = select(ResearchViewpoint).where(
                ResearchViewpoint.workspace_id == self.workspace.workspace_id,
                ResearchViewpoint.owner_id == self.workspace.user_id)
            if ticker:
                query = query.where(ResearchViewpoint.ticker == ticker.upper())
            if run_id:
                query = query.where(ResearchViewpoint.run_id == str(run_id))
            if not include_archived:
                query = query.where(ResearchViewpoint.status != "archived")
            return {"items": [self._view(row) for row in session.scalars(query.order_by(ResearchViewpoint.updated_at.desc()).limit(100)).all()]}

    def create(self, body):
        run = self.research.get_run(str(body.run_id), **self.scope)
        if run["status"] != "completed":
            raise ResearchError("viewpoint_run_incomplete", "请在研究完成后确认观点", 409)
        now = self.clock()
        try:
            with self.database.session() as session:
                self._authorize(session, write=True)
                row = ResearchViewpoint(id=str(uuid4()), **self.scope, run_id=str(body.run_id), ticker=run["ticker"],
                    **body.model_dump(exclude={"run_id", "confirmed"}), status="active", confirmed_at=now,
                    reviewed_at=None, revision=1, created_at=now, updated_at=now)
                session.add(row)
                session.flush()
                return self._view(row)
        except IntegrityError:
            raise ResearchError("viewpoint_exists", "这份研究已有确认观点，请编辑原记录", 409) from None

    def update(self, viewpoint_id, body):
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            row = self._row(session, viewpoint_id)
            if row.revision != body.revision:
                raise ResearchError("viewpoint_revision_conflict", "观点已被修改，请刷新后重试", 409)
            for key, value in body.model_dump(exclude={"revision", "mark_reviewed"}).items():
                setattr(row, key, value)
            row.revision += 1
            row.updated_at = self.clock()
            if body.mark_reviewed:
                row.reviewed_at = row.updated_at
            session.flush()
            return self._view(row)

    def compare(self, run_id, other_run_id):
        current, other = (self.research.get_run(value, **self.scope) for value in (run_id, other_run_id))
        if run_id == other_run_id or current["ticker"] != other["ticker"]:
            raise ResearchError("comparison_instrument_mismatch", "请选择同一证券的另一份研究", 422)
        first, second = (self.research.get_manifest(value, **self.scope) for value in (run_id, other_run_id))
        fields = sorted(set(first["request"]) | set(second["request"]))
        changed = [field for field in fields if first["request"].get(field) != second["request"].get(field)]
        sections = sorted(set(current["reports"]) | set(other["reports"]))
        changed_sections, diffs = [], {}
        for section in sections:
            before, after = other["reports"].get(section, ""), current["reports"].get(section, "")
            if before != after:
                changed_sections.append(section)
                diffs[section] = "\n".join(unified_diff(before.splitlines()[:200], after.splitlines()[:200],
                    fromfile="比较研究", tofile="当前研究", n=2))[:12000]
        evidence = []
        with self.database.session() as session:
            self._authorize(session)
            for value in (run_id, other_run_id):
                rows = session.execute(select(Evidence.kind, DataSnapshot.content_hash, DataSnapshot.details).join(
                    DataSnapshot, Evidence.snapshot_id == DataSnapshot.id).where(
                    Evidence.run_id == value, Evidence.workspace_id == self.workspace.workspace_id)).all()
                evidence.append(sorted(
                    (kind, digest, json.dumps({key: metadata.get(key) for key in (
                        "method", "provider", "request", "data_as_of", "status", "response_hash"
                    )}, sort_keys=True))
                    for kind, digest, metadata in rows
                ))
        verified = all(run["quality"]["status"] in {"usable", "degraded"} for run in (current, other))
        return ResearchComparison(run_id=run_id, other_run_id=other_run_id, ticker=current["ticker"],
            request_changes=changed,
            model_changed=first.get("selection", {}).get("model_snapshot") != second.get("selection", {}).get("model_snapshot"),
            same_captured_evidence=(evidence[0] == evidence[1] if verified and all(evidence) else None),
            changed_sections=changed_sections, section_diffs=diffs)
