"""Context and views are scoped, explicit and hash checked, with no model calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tradingagents.agents.research_intent import supplemental_research_context
from tradingagents.web.api.research_context_schemas import ResearchContextCreate
from tradingagents.web.api.viewpoint_schemas import ViewpointCreate, ViewpointUpdate
from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_context import ResearchContextService
from tradingagents.web.application.viewpoints import ViewpointService
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import DataSnapshot, Membership, User

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


@pytest.fixture
def followup(tmp_path):
    database = initialize_database(tmp_path / "web.sqlite3")
    local = database.local_workspace()
    research = ResearchService(database, tmp_path / "research", clock=lambda: NOW)
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    yield database, local, research, scope, tmp_path
    database.dispose()


def completed(research, scope, ticker="NVDA", **kwargs):
    run = research.create_run({"ticker": ticker, "date": "2024-01-02", **kwargs}, **scope)
    research.tasks.finish(research.tasks.claim_next("test"), "completed")
    return run


def test_view_requires_completed_run_manual_thesis_and_confirmation(followup):
    db, local, research, scope, _ = followup
    service = ViewpointService(db, local, research, clock=lambda: NOW)
    run = research.create_run({"ticker": "NVDA", "date": "2024-01-02"}, **scope)
    data = {"run_id": run["id"], "confirmed": True, "thesis": "Demand improves", "invalidation": "Guidance cut"}
    with pytest.raises(ResearchError, match="研究完成"):
        service.create(ViewpointCreate.model_validate(data))
    for patch in ({"confirmed": False}, {"confirmed": 1}, {"thesis": "  "}, {"review_on": "2026-02-30"}):
        with pytest.raises(ValidationError):
            ViewpointCreate.model_validate({**data, **patch})
    research.tasks.finish(research.tasks.claim_next("test"), "completed")
    view = service.create(ViewpointCreate.model_validate({**data, "review_on": "2026-10-24"}))
    assert view.review_due and view.source_quality == "unverified"
    assert service.list(run_id=run["id"])["items"][0].id == view.id
    with pytest.raises(ResearchError, match="已有"):
        service.create(ViewpointCreate.model_validate(data))
    update = ViewpointUpdate(revision=1, thesis="Demand improves", invalidation="Guidance cut", review_on="2026-11-01", mark_reviewed=True)
    revised = service.update(view.id, update)
    assert revised.revision == 2 and revised.reviewed_at == NOW and not revised.review_due
    with pytest.raises(ResearchError, match="已被修改"):
        service.update(view.id, update)
    archived = service.update(view.id, update.model_copy(update={"revision": 2, "status": "archived"}))
    assert archived.status == "archived" and service.list()["items"] == []


def test_views_and_comparisons_cannot_leak_other_owner(followup):
    db, local, research, scope, _ = followup
    current = completed(research, scope)
    other = completed(research, scope, horizon="long")
    service = ViewpointService(db, local, research, clock=lambda: NOW)
    result = service.compare(current["id"], other["id"])
    assert "horizon" in result.request_changes and result.same_captured_evidence is None
    foreign_stock = completed(research, scope, ticker="AAPL")
    with pytest.raises(ResearchError, match="同一证券"):
        service.compare(current["id"], foreign_stock["id"])
    with db.session() as session:
        session.add(User(id="other", name="Other"))
        session.flush()
        session.add(Membership(workspace_id=local.workspace_id, user_id="other", role="researcher"))
    foreign = SimpleNamespace(workspace_id=local.workspace_id, user_id="other")
    other_service = ViewpointService(db, foreign, research, clock=lambda: NOW)
    with pytest.raises(ResearchError, match="不存在"):
        other_service.compare(current["id"], other["id"])


class FrozenMarket:
    def __init__(self, as_of="2026-10-23"):
        self.as_of = as_of

    def get_cached_snapshot(self, snapshot_id):
        assert snapshot_id == "mkt-" + "a" * 32
        return SimpleNamespace(as_of=self.as_of, window="1D", model_dump=lambda **_: {"as_of": self.as_of, "coverage": "sample"})


def test_context_is_immutable_scoped_and_has_historical_cutoff(followup):
    db, local, _, _, root = followup
    service = ResearchContextService(db, local, root / "context", market=FrozenMarket(), clock=lambda: NOW)
    view = service.create(ResearchContextCreate(kind="market", market_snapshot_id="mkt-" + "a" * 32))
    packets = service.freeze([view.id], ticker="NVDA", analysis_date="2026-10-25")
    assert packets[0]["hash"] == view.content_hash and packets[0]["data"]["coverage"] == "sample"
    with pytest.raises(ResearchError, match="晚于研究日期"):
        service.freeze([view.id], ticker="NVDA", analysis_date="2024-01-02")
    assert view.id in supplemental_research_context(packets)
    with db.session() as session:
        row = session.scalar(select(DataSnapshot).where(DataSnapshot.id == view.id))
        from pathlib import Path
        path = Path(row.file_path)
        assert path.stat().st_mode & 0o777 == 0o600
        path.write_text("{}")
    with pytest.raises(ResearchError, match="校验失败"):
        service.freeze([view.id], ticker="NVDA", analysis_date="2026-10-25")


def test_context_requires_selection_and_capture_time_blocks_historical_backfill(followup):
    db, local, _, _, root = followup
    service = ResearchContextService(db, local, root / "context", market=FrozenMarket(None), clock=lambda: NOW)
    with pytest.raises(ResearchError, match="先加载"):
        service.create(ResearchContextCreate(kind="market"))
    view = service.create(ResearchContextCreate(kind="market", market_snapshot_id="mkt-" + "a" * 32))
    with pytest.raises(ResearchError, match="捕获晚于研究日期"):
        service.freeze([view.id], ticker="NVDA", analysis_date="2024-01-02")


def test_viewpoint_revision_is_atomic_under_concurrent_edits(followup):
    db, local, research, scope, _ = followup
    run = completed(research, scope)
    service = ViewpointService(db, local, research, clock=lambda: NOW)
    view = service.create(ViewpointCreate(run_id=run["id"], confirmed=True, thesis="Demand", invalidation="Cut"))
    def write(thesis):
        try:
            return service.update(view.id, ViewpointUpdate(revision=1, thesis=thesis, invalidation="Cut")).revision
        except ResearchError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["First", "Second"]))
    assert sorted(map(str, results)) == ["2", "viewpoint_revision_conflict"]
