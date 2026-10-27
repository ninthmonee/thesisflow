"""Restore actual LangGraph SQLite state, not reconstructed report text."""

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from tradingagents.default_config import build_default_config
from tradingagents.graph.checkpointer import (
    checkpoint_step,
    clear_checkpoint,
    get_checkpointer,
    thread_id,
)
from tradingagents.reporting import write_report_tree
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import RunAttempt
from tradingagents.web.workers.checkpoints import CheckpointFiles
from tradingagents.web.workers.research import ResearchWorker


class TinyState(TypedDict, total=False):
    market_report: str
    final_trade_decision: str
    final_rating: str
    internal_value: int


class CheckpointGraph:
    def __init__(self, analysts, config, callbacks, control):
        self.config, self.control = config, control
        workflow = StateGraph(TinyState)

        def analyze(state):
            control["analysis_calls"] += 1
            return {"market_report": "saved analysis", "internal_value": 37}

        def decide(state):
            control["decision_calls"] += 1
            if control["fail"]:
                control["fail"] = False
                raise RuntimeError("injected node failure")
            assert state["internal_value"] == 37
            return {"final_trade_decision": "Final Rating: Buy", "final_rating": "Buy"}

        workflow.add_node("analyze", analyze)
        workflow.add_node("decide", decide)
        workflow.add_edge(START, "analyze")
        workflow.add_edge("analyze", "decide")
        workflow.add_edge("decide", END)
        self.workflow = workflow
        self.graph = workflow.compile()
        self.propagator = self
        self._context = None
        self._resuming = False

    def _run_signature(self, asset_type):
        return self.control["signature"]

    def get_graph_args(self, **kwargs):
        return {}

    def create_run_state(self, *args):
        return {"internal_value": 0}

    def begin_checkpoint(self, ticker, research_date, asset_type):
        self._context = get_checkpointer(self.config["data_cache_dir"], ticker)
        saver = self._context.__enter__()
        self.graph = self.workflow.compile(checkpointer=saver)
        signature = self._run_signature(asset_type)
        self._resuming = (
            checkpoint_step(self.config["data_cache_dir"], ticker, research_date, signature)
            is not None
        )
        return thread_id(ticker, research_date, signature)

    def checkpoint_input(self, initial):
        return None if self._resuming else initial

    def stream_run(self, state, **kwargs):
        for update in self.graph.stream(state, stream_mode="updates", **kwargs):
            for chunk in update.values():
                yield [], chunk

    def save_reports(self, state, ticker, save_path, html):
        write_report_tree(state, ticker, save_path, html=html)

    def record_decision(self, *args):
        pass

    def clear_checkpoint_on_success(self, ticker, research_date, asset_type):
        clear_checkpoint(
            self.config["data_cache_dir"], ticker, research_date, self._run_signature(asset_type)
        )

    def end_checkpoint(self):
        if self._context is not None:
            self._context.__exit__(None, None, None)


@pytest.fixture
def checkpoint_research(tmp_path):
    database = initialize_database(tmp_path / "app.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, tmp_path / "artifacts")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    control = {"fail": True, "analysis_calls": 0, "decision_calls": 0, "signature": "test-graph-v1"}

    def factory(analysts, config, callbacks):
        return CheckpointGraph(analysts, config, callbacks, control)

    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=factory)
    run = service.create_run({"ticker": "NVDA", "date": "2024-01-02"}, **scope)
    yield service, scope, worker, run, control
    database.dispose()


def test_resume_restores_complete_graph_state_and_does_not_repeat_completed_node(
    checkpoint_research,
):
    service, scope, worker, run, control = checkpoint_research
    worker.run_once()
    assert service.get_run(run["id"], **scope)["status"] == "failed"
    assert control["analysis_calls"] == 1
    with service.database.session() as session:
        first_attempt = session.scalar(select(RunAttempt).where(RunAttempt.run_id == run["id"]))
        first_reference = first_attempt.checkpoint_ref
    assert first_reference
    assert service.resume_run(run["id"], **scope)["status"] == "queued"
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "completed"
    assert result["reports"]["market_report"] == "saved analysis"
    assert control["analysis_calls"] == 1
    assert control["decision_calls"] == 2
    with service.database.session() as session:
        attempts = session.scalars(
            select(RunAttempt).where(RunAttempt.run_id == run["id"]).order_by(RunAttempt.attempt_no)
        ).all()
        assert [attempt.attempt_no for attempt in attempts] == [1, 2]
        assert attempts[0].checkpoint_ref != attempts[1].checkpoint_ref


def test_changed_graph_signature_is_rejected_before_another_node_runs(checkpoint_research):
    service, scope, worker, run, control = checkpoint_research
    worker.run_once()
    control["signature"] = "changed-graph-v2"
    service.resume_run(run["id"], **scope)
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "failed"
    assert "执行图配置已改变" in result["error"]
    assert control["analysis_calls"] == 1
    assert control["decision_calls"] == 1


def test_changed_code_build_prevents_resume_enqueue(checkpoint_research, monkeypatch):
    service, scope, worker, run, control = checkpoint_research
    worker.run_once()
    monkeypatch.setattr(
        "tradingagents.web.infrastructure.runtime_contract.current_graph_contract",
        lambda: "web-research-v2:changed-prompt-and-schema",
    )
    with pytest.raises(ResearchError) as error:
        service.resume_run(run["id"], **scope)
    assert error.value.code == "graph_contract_incompatible"
    assert service.get_run(run["id"], **scope)["status"] == "failed"
    assert control["analysis_calls"] == control["decision_calls"] == 1


def test_changed_code_build_prevents_queued_task_execution(checkpoint_research, monkeypatch):
    service, scope, worker, run, control = checkpoint_research
    monkeypatch.setattr(
        "tradingagents.web.infrastructure.runtime_contract.current_graph_contract",
        lambda: "web-research-v2:new-build",
    )
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "failed"
    assert "代码、提示词" in result["error"]
    assert control["analysis_calls"] == control["decision_calls"] == 0


def test_checkpoint_tampering_is_detected_before_resume_enqueue(checkpoint_research):
    service, scope, worker, run, _control = checkpoint_research
    worker.run_once()
    with service.database.session() as session:
        attempt = session.scalar(select(RunAttempt).where(RunAttempt.run_id == run["id"]))
        reference = attempt.checkpoint_ref
    manifest = service.get_manifest(run["id"], **scope)
    from tradingagents.web.application.research import content_hash

    _metadata, database = CheckpointFiles(service).validate_ref(
        reference, scope["workspace_id"], run["id"], content_hash(manifest)
    )
    with database.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ResearchError) as captured:
        service.resume_run(run["id"], **scope)
    assert captured.value.code == "checkpoint_invalid"
    assert service.get_run(run["id"], **scope)["status"] == "failed"
