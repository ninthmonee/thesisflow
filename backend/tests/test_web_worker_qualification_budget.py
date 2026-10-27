"""Worker qualification, concurrent budget reservations and returned usage, offline."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tradingagents.dataflows.evidence import observed_call
from tradingagents.default_config import build_default_config
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import ResearchRun
from tradingagents.web.workers.research import ResearchWorker, WorkerCallbacks

MARKET = "Date,Open,High,Low,Close,Volume\n2024-01-02,100,110,95,105,1000\n"


@pytest.fixture
def research(tmp_path):
    database = initialize_database(tmp_path / "web.sqlite3")
    local = database.local_workspace()
    service = ResearchService(database, tmp_path / "artifacts")
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    yield service, scope
    database.dispose()


class EvidenceGraph:
    snapshots = []
    use_evidence = True

    def __init__(self, analysts, config, callbacks):
        self.config, self.callbacks = config, callbacks
        self.propagator = SimpleNamespace(get_graph_args=lambda **kwargs: {})

    def create_run_state(self, ticker, research_date, asset_type):
        return {"company_of_interest": ticker, "trade_date": research_date}

    def begin_checkpoint(self, *args):
        return None

    def checkpoint_input(self, state):
        return state

    def stream_run(self, state, **kwargs):
        if self.use_evidence:
            observed_call(
                "get_stock_data",
                "yfinance",
                ("NVDA", "2023-12-01", "2024-01-02"),
                {},
                lambda: MARKET,
            )
        yield [], {"market_report": "source-backed technical report"}
        yield [], {"final_trade_decision": "Rating: Buy\nRaw model thesis", "final_rating": "Buy"}

    def save_reports(self, state, ticker, save_path, html):
        self.snapshots.append(state)

    def record_decision(self, *args):
        self.snapshots.append(args[-1])

    def clear_checkpoint_on_success(self, *args):
        pass

    def end_checkpoint(self):
        pass


@pytest.mark.parametrize("has_evidence, expected", [(True, "usable"), (False, "insufficient")])
def test_completion_is_separate_from_quality_and_raw_reports_remain(
    research, has_evidence, expected
):
    service, scope = research
    run = service.create_run(
        {"ticker": "NVDA", "date": "2024-01-02", "analysts": ["market"]}, **scope
    )

    class Graph(EvidenceGraph):
        use_evidence = has_evidence
        snapshots = []

    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=Graph)
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "completed" and result["quality"]["status"] == expected
    assert result["raw_rating"] == "Buy"
    assert result["rating"] == ("Buy" if has_evidence else None)
    assert result["reports"]["final_trade_decision"] == "Rating: Buy\nRaw model thesis"
    assert Graph.snapshots[-1]["final_rating"] == ("Buy" if has_evidence else "REVIEW")
    if not has_evidence:
        assert "数据资格未通过" in Graph.snapshots[0]["final_trade_decision"]


def _callbacks(*, prior=None, budget=None, active=lambda: None):
    published = []
    service = SimpleNamespace(
        tasks=SimpleNamespace(publish=lambda *args, **kwargs: published.append(kwargs))
    )
    callback = WorkerCallbacks(service, None, active, time.monotonic(), prior_stats=prior)
    callback.budget = budget or {}
    return callback, published


def test_parallel_starts_reserve_budget_atomically_without_sending_extra_call():
    callback, _ = _callbacks(budget={"max_llm_calls": 2})
    barrier = threading.Barrier(8)

    def start(index):
        barrier.wait()
        try:
            callback.on_llm_start({}, [], run_id=f"call-{index}")
            return True
        except ResearchError as error:
            assert error.code == "research_budget_exceeded"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(start, range(8)))
    assert sum(outcomes) == 2
    assert callback.stats()["llm_calls"] == 2 and callback.stats()["uncertain_calls"] == 2


def test_prior_attempt_calls_count_towards_the_same_run_budget():
    callback, _ = _callbacks(prior={"llm_calls": 2}, budget={"max_llm_calls": 2})
    with pytest.raises(ResearchError, match="预算"):
        callback.on_llm_start({}, [], run_id="must-not-start")
    assert callback.stats()["llm_calls"] == 0


def test_usage_is_recorded_before_cancellation_and_deduplicated():
    cancelled = False

    def active():
        if cancelled:
            raise ResearchError("run_cancelled", "cancelled")

    callback, published = _callbacks(active=active)
    callback.on_llm_start({}, [], run_id="call")
    cancelled = True
    response = SimpleNamespace(
        llm_output={"token_usage": {"prompt_tokens": 12, "completion_tokens": 8}}
    )
    with pytest.raises(ResearchError, match="cancelled"):
        callback.on_llm_end(response, run_id="call")
    assert published[-1]["stats"]["tokens_in"] == 12
    assert (
        callback.stats()["usage_reported_calls"] == 1 and callback.stats()["uncertain_calls"] == 0
    )
    callback.on_llm_end(response, run_id="call")
    assert callback.stats()["tokens_out"] == 8


def test_missing_or_incomplete_usage_is_uncertain_and_errors_never_expose_body():
    callback, published = _callbacks()
    callback.on_llm_start({}, [], run_id="call")
    callback.on_llm_end(SimpleNamespace(llm_output={}), run_id="call")
    callback.on_llm_error(RuntimeError("api_key=private-secret"), run_id="call")
    assert callback.stats()["usage_quality"] == "unknown"
    assert callback.stats()["uncertain_calls"] == 1
    assert "private-secret" not in json.dumps(published)


def test_returned_tokens_stop_later_work_without_erasing_usage():
    callback, published = _callbacks(
        prior={"tokens_in": 8, "tokens_out": 2}, budget={"max_tokens_total": 20}
    )
    callback.on_llm_start({}, [], run_id="call")
    callback.on_llm_end(
        SimpleNamespace(llm_output={"usage": {"input_tokens": 5, "output_tokens": 5}}),
        run_id="call",
    )
    assert published[-1]["stats"]["tokens_in"] == 5
    with pytest.raises(ResearchError, match="预算"):
        callback.check_budget()


def test_worker_enforces_budget_across_attempts_and_preserves_partial_reports(research):
    service, scope = research
    run = service.create_run(
        {"ticker": "NVDA", "date": "2024-01-02", "analysts": ["market"], "max_llm_calls": 1},
        **scope,
    )

    class Graph(EvidenceGraph):
        def stream_run(self, state, **kwargs):
            yield [], {"market_report": "partial survives"}
            self.callbacks[0].on_llm_start({}, [], run_id="first")
            self.callbacks[0].on_llm_start({}, [], run_id="second")
            yield [], {"final_trade_decision": "must not publish"}

    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=Graph)
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "failed" and result["reports"]["market_report"] == "partial survives"
    assert result["stats"]["llm_calls"] == 1 and result["stats"]["uncertain_calls"] == 1
    # Isolated repository fixture re-queues the same manifest; no budget can reset.
    with service.database.session() as session:
        session.get(ResearchRun, run["id"]).status = "queued"
    worker.run_once()
    result = service.get_run(run["id"], **scope)
    assert result["status"] == "failed" and result["stats"]["llm_calls"] == 1
    assert result["stats"]["attempt_count"] == 2


def test_frozen_objective_and_context_are_passed_as_allowlisted_runtime_inputs(research):
    service, scope = research
    run = service.create_run(
        {
            "ticker": "NVDA",
            "date": "2024-01-02",
            "analysts": ["market"],
            "objective": "technical",
            "horizon": "short",
            "holding_state": "held",
            "context_ids": ["00000000-0000-4000-8000-000000000001"],
        },
        manifest={
            "context_inputs": [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "kind": "market",
                    "hash": "a" * 64,
                    "data": {"coverage": "sample"},
                }
            ]
        },
        **scope,
    )
    worker = ResearchWorker(service, lambda _: build_default_config(), graph_factory=EvidenceGraph)
    claim = service.tasks.claim_next("runtime-test", 90)
    manifest = service.get_manifest(run["id"], **scope)
    config, _ = worker._runtime(claim, manifest)
    assert config["research_request"] == {
        "objective": "technical",
        "horizon": "short",
        "holding_state": "held",
    }
    assert config["research_context"][0]["id"] == "00000000-0000-4000-8000-000000000001"
