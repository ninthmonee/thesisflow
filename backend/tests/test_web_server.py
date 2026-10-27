"""Web boundary and graph integration, entirely offline and without real model calls."""

import json
import threading
import time
import uuid
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.propagation import Propagator
from tradingagents.reporting import write_report_tree
from tradingagents.web.server import RunStore, create_app

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


class FakeGraph:
    """Exercises the real worker contract; never constructs an LLM or data client."""

    instances = []
    gate = None
    failure = None

    def __init__(self, selected_analysts, config, callbacks):
        self.selected_analysts = selected_analysts
        self.config = config
        self.callback = callbacks[0]
        self.propagator = Propagator()
        self.calls = []
        self.__class__.instances.append(self)

    def create_run_state(self, ticker, trade_date, asset_type):
        self.calls.append(("create", ticker, trade_date, asset_type))
        return self.propagator.create_initial_state(ticker, trade_date, asset_type)

    def begin_checkpoint(self, *args):
        self.calls.append(("begin",))
        return "test-checkpoint" if self.config.get("checkpoint_enabled") else None

    def checkpoint_input(self, state):
        self.calls.append(("checkpoint_input",))
        return state

    def stream_run(self, state, **args):
        self.calls.append(("stream", args))
        if self.gate is not None:
            assert self.gate.wait(5), "test did not release the graph"
        self.callback.on_chat_model_start({}, [], run_id="model-1")
        self.callback.on_llm_start({}, [], run_id="model-1")  # Same run must count once.
        usage = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20})
        self.callback.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=usage)]]))
        self.callback.on_tool_start({}, "TOP-SECRET /private/key.txt")
        # Repeated messages must never be published to the browser event log.
        message = SimpleNamespace(id="one", content="TOP-SECRET /private/key.txt")
        yield [message], {"market_report": "# 技术分析\n价格趋势向上。"}
        yield [message], None
        if self.failure:
            raise self.failure
        yield [], {"news_report": "公司发布新产品。", "investment_debate_state": {
            "bull_history": "多头观点", "bear_history": "空头观点", "history": "辩论"}}
        yield [], {"investment_plan": "研究经理建议关注。", "trader_investment_plan": "控制仓位。"}
        yield [], {"risk_debate_state": {"aggressive_history": "激进观点", "conservative_history": "保守观点",
                                         "neutral_history": "中立观点", "history": "风险辩论"}}
        yield [], {"final_trade_decision": "Rating: Buy\n谨慎评估风险。", "final_rating": "Buy"}

    def save_reports(self, state, ticker, save_path, html):
        self.calls.append(("save",))
        return write_report_tree(state, ticker, save_path, settings={"version": "test"}, html=html)

    def record_decision(self, *args):
        self.calls.append(("record",))

    def clear_checkpoint_on_success(self, *args):
        self.calls.append(("clear",))

    def end_checkpoint(self):
        self.calls.append(("end",))


@pytest.fixture
def web(tmp_path, monkeypatch):
    FakeGraph.instances = []
    FakeGraph.gate = None
    FakeGraph.failure = None
    monkeypatch.setenv("OPENAI_API_KEY", "TOP-SECRET")
    config = deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(tmp_path / "results"), backend_url="https://secret:password@example.com/v1",
                  llm_provider="openai", quick_think_provider=None, deep_think_provider=None)
    app = create_app(config, graph_factory=FakeGraph, authentication_required=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, app.state.store, config
        if FakeGraph.gate:
            FakeGraph.gate.set()
        for thread in list(app.state.store.threads.values()):
            thread.join(5)


def submit(client, **overrides):
    body = {"ticker": "NVDA", "date": "2025-06-20", "analysts": ["market", "news"],
            "depth": 1, "language": "Chinese"}
    body.update(overrides)
    return client.post("/api/runs", json=body, headers=ORIGIN)


def finish(client, run_id):
    for _ in range(200):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed", "interrupted"}:
            return run
        time.sleep(0.01)
    pytest.fail("background graph did not finish")


def test_config_is_allowlisted_and_key_status_is_local(web):
    client, _store, _config = web
    response = client.get("/api/config")
    data = response.json()
    assert data["model_ready"] is True
    assert data["version"] == "0.7.0"
    assert "quick_model" in data and "deep_model" in data
    assert "TOP-SECRET" not in response.text
    assert "password" not in response.text
    assert "backend_url" not in response.text
    assert "/Users/" not in response.text


def test_complete_worker_stream_and_report_downloads(web):
    client, store, config = web
    response = submit(client, ticker="nvda", depth=2, checkpoint=True)
    assert response.status_code == 202
    run = finish(client, response.json()["id"])
    assert run["status"] == "completed", run["error"]
    assert run["ticker"] == "NVDA"
    assert run["rating"] == "Buy"
    assert run["reports"]["market_report"].startswith("# 技术")
    assert run["reports"]["bull_history"] == "多头观点"
    assert run["reports"]["conservative_history"] == "保守观点"
    assert run["stats"] == {"llm_calls": 1, "tool_calls": 1, "tokens_in": 100, "tokens_out": 20,
                            "elapsed_seconds": run["stats"]["elapsed_seconds"]}
    assert "TOP-SECRET" not in json.dumps(run)
    assert "/private/key" not in json.dumps(run)
    graph = FakeGraph.instances[0]
    names = [call[0] for call in graph.calls]
    assert names == ["create", "begin", "checkpoint_input", "stream", "save", "record", "clear", "end"]
    assert graph.config["max_debate_rounds"] == 2
    assert graph.calls[3][1]["config"]["configurable"]["thread_id"] == "test-checkpoint"
    assert config["results_dir"] != graph.config["results_dir"]
    assert store.active_id is None
    markdown = client.get(run["markdown_url"])
    assert markdown.status_code == 200
    assert "attachment" in markdown.headers["content-disposition"]
    assert "Rating: Buy" in markdown.text
    html = client.get(run["report_url"])
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "Content-Security-Policy" in html.headers
    assert (store.root / run["id"] / "run.json").is_file()


def test_subgraph_report_persists_when_later_graph_fails(web):
    client, store, _config = web
    FakeGraph.failure = RuntimeError("Bearer TOP-SECRET https://secret:password@example.com /private/key.txt")
    run = finish(client, submit(client).json()["id"])
    assert run["status"] == "failed"
    assert run["reports"]["market_report"]
    assert "TOP-SECRET" not in json.dumps(run)
    assert "password" not in json.dumps(run)
    assert "private" not in run["error"]
    assert run["report_url"] is None
    names = [call[0] for call in FakeGraph.instances[0].calls]
    assert names[-1] == "end"
    assert "record" not in names and "clear" not in names
    assert store.active_id is None


def test_running_lock_and_rejection_do_not_start_extra_graph(web):
    client, store, _config = web
    FakeGraph.gate = threading.Event()
    first = submit(client)
    assert first.status_code == 202
    run_id = first.json()["id"]
    second = submit(client)
    assert second.status_code == 409
    assert second.json()["detail"]["run_id"] == run_id
    assert len(FakeGraph.instances) == 1
    assert store.active_id == run_id
    FakeGraph.gate.set()
    assert finish(client, run_id)["status"] == "completed"


@pytest.mark.parametrize("input_change", [
    {"ticker": "../../private/key"}, {"ticker": "."}, {"ticker": "A" * 33},
    {"ticker": "NVDA\nsecret"}, {"date": "2025-6-20"}, {"date": "2025-02-30"},
    {"date": (date.today() + timedelta(days=10)).isoformat()}, {"date": "1969-12-31"},
    {"depth": 0}, {"depth": 4}, {"depth": "1"}, {"depth": True},
    {"analysts": []}, {"analysts": ["market", "market"]}, {"analysts": ["unknown"]},
    {"language": "Chinese; execute code"}, {"backend_url": "http://attacker"},
    {"config": {"api_key": "TOP-SECRET"}},
])
def test_invalid_inputs_rejected_before_graph(web, input_change):
    client, store, _config = web
    response = submit(client, **input_change)
    assert response.status_code == 422
    assert not store.history()
    assert not FakeGraph.instances
    assert "TOP-SECRET" not in response.text


@pytest.mark.parametrize("ticker, canonical", [("BTCUSDT", "BTC-USD"), ("UNI-USD", "UNI-USD"), ("ETH-BTC", "ETH-BTC")])
def test_crypto_is_normalized_and_corporate_fundamentals_removed(web, ticker, canonical):
    client, _store, _config = web
    response = submit(client, ticker=ticker, analysts=["fundamentals", "market"])
    assert response.status_code == 202
    run = finish(client, response.json()["id"])
    assert run["ticker"] == canonical and run["asset_type"] == "crypto"
    assert run["analysts"] == ["market"]


def test_missing_model_configuration_never_starts_work(web, monkeypatch):
    client, _store, _config = web
    monkeypatch.delenv("OPENAI_API_KEY")
    assert client.get("/api/config").json()["model_ready"] is False
    assert submit(client).status_code == 503
    assert not FakeGraph.instances


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://localhost:8000"}, {**ORIGIN, "Sec-Fetch-Site": "cross-site"},
    {**ORIGIN, "Host": "evil.example"}, {"Origin": "http://127.0.0.1.evil:8000"},
])
def test_cross_site_requests_cannot_trigger_paid_analysis(web, headers):
    client, store, _config = web
    response = client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-06-20"}, headers=headers)
    assert response.status_code == 403
    assert not store.history()


def test_unknown_ids_and_oversized_body_are_rejected(web):
    client, _store, _config = web
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    assert client.get(f"/api/runs/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/config", headers={"Host": "evil.example"}).status_code == 403
    response = client.post("/api/runs", content="x" * 9000,
                           headers={**ORIGIN, "Content-Type": "application/json"})
    assert response.status_code == 413


def test_external_top_level_navigation_is_allowed_but_fetch_and_writes_are_not(web):
    client, store, _config = web
    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                  "Sec-Fetch-Dest": "document"}
    # The shipped root should open from Chrome extensions, bookmarks or links.
    root = client.get("/", headers=navigation)
    assert root.status_code != 403
    assert client.get("/api/config", headers=navigation).status_code == 200
    external_fetch = {**navigation, "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
    assert client.get("/api/config", headers=external_fetch).status_code == 403
    iframe = {**navigation, "Sec-Fetch-Dest": "iframe"}
    assert client.get("/", headers=iframe).status_code == 403
    assert client.get("/", headers={**navigation, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-06-20"},
                       headers=navigation).status_code == 403
    assert not store.history()


def test_history_and_reports_survive_new_store(web):
    client, store, config = web
    run = finish(client, submit(client).json()["id"])
    restored = RunStore(config, graph_factory=FakeGraph)
    assert restored.history()[0]["id"] == run["id"]
    assert restored.snapshot(run["id"])["reports"] == run["reports"]
    assert restored.active_id is None
    assert len(FakeGraph.instances) == 1


def test_restart_marks_unfinished_snapshots_interrupted(web):
    _client, store, config = web
    run_id = str(uuid.uuid4())
    run = {"id": run_id, "status": "running", "stage": "analysts", "created_at": "2025-06-20T00:00:00+00:00",
           "revision": 1, "stats": {}, "reports": {}, "events": []}
    directory = store.root / run_id
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    restored = RunStore(config, graph_factory=FakeGraph)
    assert restored.snapshot(run_id)["status"] == "interrupted"
    assert restored.active_id is None
    saved = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert saved["finished_at"] and saved["error"]


def test_sse_returns_terminal_snapshot_and_can_resume(web):
    client, _store, _config = web
    run = finish(client, submit(client).json()["id"])
    response = client.get(f"/api/runs/{run['id']}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: update" in response.text and "event: done" in response.text
    assert f"id: {run['revision']}" in response.text
    resumed = client.get(f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(run["revision"])})
    assert "event: update" not in resumed.text
    assert "event: done" in resumed.text


def test_stored_raw_messages_and_configs_are_not_exposed(web):
    client, store, _config = web
    run = finish(client, submit(client).json()["id"])
    saved = (store.root / run["id"] / "run.json").read_text(encoding="utf-8")
    for forbidden in ("TOP-SECRET", "password", "backend_url", "instrument_context", "past_context", "messages"):
        assert forbidden not in saved
    assert "TOP-SECRET" not in client.get("/api/runs").text
