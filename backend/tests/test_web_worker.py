"""Real durable-worker control flow with injected offline execution graphs."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.default_config import build_default_config
from tradingagents.reporting import write_report_tree
from tradingagents.web.api.schemas import EventEnvelope, RunSnapshot
from tradingagents.web.application.research import REPORT_LABELS, ResearchService
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.workers.research import ResearchWorker


@pytest.fixture
def research(tmp_path):
    database = initialize_database(tmp_path / "app.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, tmp_path / "artifacts")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    yield service, scope
    database.dispose()


class OfflineGraph:
    configurations = []

    def __init__(self, analysts, config, callbacks):
        self.config, self.callbacks = config, callbacks
        self.configurations.append(config)
        self.propagator = SimpleNamespace(get_graph_args=lambda **kwargs: {})

    def create_run_state(self, ticker, research_date, asset_type):
        return {"company_of_interest": ticker, "trade_date": research_date}

    def begin_checkpoint(self, *args):
        return None

    def checkpoint_input(self, state):
        return state

    def stream_run(self, state, **kwargs):
        callback = self.callbacks[0]
        callback.on_llm_start({}, ["raw-private-prompt"], run_id="llm-1")
        callback.on_tool_start({}, "raw-private-tool-input")
        callback.on_llm_end(
            SimpleNamespace(
                llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 20}}
            )
        )
        yield (
            ["raw-private-message"],
            {
                "market_report": "market",
                "sentiment_report": "sentiment",
                "news_report": "news",
                "fundamentals_report": "fundamentals",
            },
        )
        yield (
            [],
            {
                "investment_debate_state": {
                    "bull_history": "bull",
                    "bear_history": "bear",
                    "history": "debate",
                },
                "investment_plan": "research-manager",
                "trader_investment_plan": "trader",
            },
        )
        yield (
            [],
            {
                "risk_debate_state": {
                    "aggressive_history": "aggressive",
                    "conservative_history": "conservative",
                    "neutral_history": "neutral",
                    "history": "risk",
                },
                "memory_note": "isolated",
                "final_trade_decision": "Final Rating: Buy",
                "final_rating": "Buy",
            },
        )

    def save_reports(self, state, ticker, save_path, html):
        write_report_tree(state, ticker, save_path, html=html)

    def record_decision(self, *args):
        pass

    def clear_checkpoint_on_success(self, *args):
        pass

    def end_checkpoint(self):
        pass


def create(service, scope):
    return service.create_run({"ticker": "NVDA", "date": "2024-01-02"}, **scope)


def test_offline_worker_preserves_all_core_sections_and_safe_progress(research):
    service, scope = research
    run = create(service, scope)
    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=OfflineGraph)
    assert worker.run_once() is True
    assert worker.run_once() is False
    result = service.get_run(run["id"], **scope)
    RunSnapshot.model_validate(result)
    assert result["status"] == "completed"
    assert result["rating"] is None
    assert result["raw_rating"] == "Buy"
    assert result["quality"]["status"] == "insufficient"
    assert set(result["reports"]) == set(REPORT_LABELS)
    assert result["stats"]["llm_calls"] == 1
    assert result["stats"]["tool_calls"] == 1
    assert result["stats"]["tokens_in"] == 10
    assert result["stats"]["tokens_out"] == 20
    for event in service.list_events(run["id"], limit=1000, **scope):
        EventEnvelope.model_validate(event)
    assert "raw-private" not in json.dumps(result)
    assert service.report_file(run["id"], **scope).is_file()
    assert service.report_file(run["id"], "md", **scope).is_file()


def test_worker_paths_are_isolated_by_run_and_attempt(research):
    service, scope = research
    OfflineGraph.configurations = []
    for _ in range(2):
        create(service, scope)
    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=OfflineGraph)
    worker.run_once()
    worker.run_once()
    assert len(OfflineGraph.configurations) == 2
    for key in ("results_dir", "data_cache_dir", "memory_log_path"):
        values = [Path(config[key]) for config in OfflineGraph.configurations]
        assert values[0] != values[1]
        assert all(value.is_relative_to(service.artifacts_root) for value in values)


def test_running_cancel_keeps_partial_reports_and_never_completes(research):
    service, scope = research
    run = create(service, scope)

    class CancellingGraph(OfflineGraph):
        def stream_run(self, state, **kwargs):
            yield [], {"market_report": "partial"}
            service.cancel_run(run["id"], **scope)
            yield [], {"final_trade_decision": "must not publish"}

    worker = ResearchWorker(
        service, lambda _: build_default_config(), graph_factory=CancellingGraph
    )
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "cancelled"
    assert result["reports"] == {"market_report": "partial"}
    assert result["report_url"] is None


def test_failure_never_publishes_upstream_secret_text(research):
    service, scope = research
    run = create(service, scope)

    class BrokenGraph(OfflineGraph):
        def stream_run(self, state, **kwargs):
            raise RuntimeError("Authorization: Bearer sk-private-credential; /private/path")
            yield

    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=BrokenGraph)
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "failed"
    assert "sk-private" not in json.dumps(result)
    assert "/private/path" not in json.dumps(service.list_events(run["id"], **scope))


def test_config_resolver_receives_callbacks_but_private_factory_is_not_persisted(research):
    service, scope = research
    run = create(service, scope)
    seen = []

    def resolve(manifest, *, callbacks=None):
        seen.extend(callbacks)
        return {
            **build_default_config(),
            "__role_model_resolver": lambda role: None,
            "__role_model_signature": "role-config-v1",
        }

    worker = ResearchWorker(service, resolve, graph_factory=OfflineGraph)
    worker.run_once()
    assert len(seen) == 1
    assert "__role_model" not in json.dumps(service.get_manifest(run["id"], **scope))
    assert service.get_run(run["id"], **scope)["status"] == "completed"


def test_deadline_fails_before_model_execution(research):
    service, scope = research
    run = create(service, scope)
    worker = ResearchWorker(
        service,
        lambda _: build_default_config(),
        graph_factory=OfflineGraph,
        max_run_seconds=0.000001,
    )
    worker.run_once()
    assert service.get_run(run["id"], **scope)["status"] == "failed"
    assert service.get_run(run["id"], **scope)["reports"] == {}

