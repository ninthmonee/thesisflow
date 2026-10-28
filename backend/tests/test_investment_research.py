"""Frozen investment discipline reaches real role prompts and both execution paths."""

import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from tradingagents.agents.research_intent import investment_system_context
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.executors.investment import investment_checklist, investment_prompt
from tradingagents.web.executors.local_agent import LocalAgentExecutor

DISCIPLINE = "POLICY_SENTINEL：先看宏观、行业、估值周期；概率未知不补齐，仓位未知不计算买入数量。"


def initial_state():
    graph = SimpleNamespace(
        config={"investment_system_prompt": DISCIPLINE},
        propagator=Propagator(), resolve_instrument_context=lambda *_: "Instrument: NVDA.",
    )
    state = TradingAgentsGraph.create_run_state(graph, "NVDA", "2024-01-02")
    state.update(market_report="Synthetic price evidence", news_report="Synthetic news evidence",
        sentiment_report="Synthetic sentiment evidence", fundamentals_report="Synthetic company evidence",
        investment_plan="Synthetic research plan", trader_investment_plan="Synthetic trade plan")
    return state


def manifest_for(workspace_id="workspace", owner_id="owner"):
    snapshot = {"version": 1, "workspace_id": workspace_id, "owner_id": owner_id,
        "policy": {"name": "合成规则", "revision": 3, "enabled": True}, "plan": None,
        "private_cash": "PRIVATE_CAPITAL_DO_NOT_SEND", "life_notes": "PRIVATE_LIFE_DO_NOT_SEND"}
    snapshot["content_hash"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"workspace_id": workspace_id, "owner_id": owner_id,
        "request": {"ticker": "NVDA", "date": "2024-01-02", "apply_investment_system": True},
        "selection": {"mode": "local", "investment_system": {"snapshot": snapshot, "prompt": DISCIPLINE}}}


class CapturingModel(Runnable):
    def __init__(self):
        self.seen = []

    def invoke(self, prompt, config=None, **kwargs):
        self.seen.append(prompt.to_string() if hasattr(prompt, "to_string") else str(prompt))
        return AIMessage(content="Synthetic role response. No investment recommendation.")

    def bind_tools(self, *_args, **_kwargs):
        return self

    def with_structured_output(self, *_args, **_kwargs):
        raise NotImplementedError


@pytest.mark.parametrize("module,factory", [
    ("analysts.market_analyst", "create_market_analyst"),
    ("analysts.sentiment_analyst", "create_sentiment_analyst"),
    ("analysts.news_analyst", "create_news_analyst"),
    ("analysts.fundamentals_analyst", "create_fundamentals_analyst"),
    ("researchers.bull_researcher", "create_bull_researcher"),
    ("researchers.bear_researcher", "create_bear_researcher"),
    ("managers.research_manager", "create_research_manager"),
    ("trader.trader", "create_trader"),
    ("risk_mgmt.aggressive_debator", "create_aggressive_debator"),
    ("risk_mgmt.conservative_debator", "create_conservative_debator"),
    ("risk_mgmt.neutral_debator", "create_neutral_debator"),
    ("managers.portfolio_manager", "create_portfolio_manager"),
])
def test_frozen_discipline_reaches_every_actual_role_prompt(module, factory, monkeypatch):
    sentiment = importlib.import_module("tradingagents.agents.analysts.sentiment_analyst")
    monkeypatch.setattr(sentiment, "fetch_stocktwits_messages", lambda *_a, **_k: "No source sample")
    monkeypatch.setattr(sentiment, "fetch_reddit_posts", lambda *_a, **_k: "No source sample")
    monkeypatch.setattr(sentiment.get_news, "func", lambda *_a, **_k: "No source sample")
    target = importlib.import_module("tradingagents.agents." + module)
    model = CapturingModel()
    getattr(target, factory)(model)(initial_state())
    text = "\n".join(model.seen)
    assert DISCIPLINE in text
    assert "not verified market or portfolio evidence" in text
    assert "cannot grant access" in text
    assert "stop-loss is not a guaranteed maximum loss" in text


def test_unset_and_unbounded_discipline_do_not_change_the_shared_context():
    graph = SimpleNamespace(config={}, propagator=Propagator(), resolve_instrument_context=lambda *_: "Identity.")
    assert TradingAgentsGraph.create_run_state(graph, "NVDA", "2024-01-02")["instrument_context"] == "Identity."
    for value in (None, {"capital_total_usd": 99999999}, " ", "x" * 64_001):
        assert investment_system_context(value) == ""


def test_disabled_request_ignores_host_and_stale_selected_policy():
    manifest = {"request": {"apply_investment_system": False},
        "selection": {"investment_system": {"snapshot": {"private_cash": "DO_NOT_SEND"}, "prompt": DISCIPLINE}}}
    assert investment_prompt(manifest) == investment_checklist(manifest) == ""
    local = LocalAgentExecutor._prompt(manifest)
    assert "POLICY_SENTINEL" not in local and "DO_NOT_SEND" not in local


@pytest.mark.parametrize("packet", [None, {}, {"snapshot": {}, "prompt": " "}, {"snapshot": {}, "prompt": "x" * 64_001}])
def test_enabled_malformed_policy_fails_before_a_model_is_started(packet):
    with pytest.raises(ResearchError) as invalid:
        investment_prompt({"request": {"apply_investment_system": True}, "selection": {"investment_system": packet}})
    assert invalid.value.code in {"investment_system_invalid", "investment_system_scope_invalid"}


def test_the_frozen_discipline_changes_the_checkpoint_signature():
    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts = ["market"]
    graph.config = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}
    plain = graph._run_signature("stock")
    graph.config["investment_system_prompt"] = DISCIPLINE
    original = graph._run_signature("stock")
    graph.config["investment_system_prompt"] = DISCIPLINE + " amended"
    assert len({plain, original, graph._run_signature("stock")}) == 3


def test_local_agent_uses_only_frozen_research_prompt_and_checklist_does_not_rate_it():
    manifest = manifest_for()
    prompt = LocalAgentExecutor._prompt(manifest)
    checklist = investment_checklist(manifest)
    assert DISCIPLINE in prompt and "版本 3" in checklist
    assert checklist.count("待人工核对：") == 6
    assert "不证明模型已逐项遵守" in checklist
    for private in ("PRIVATE_CAPITAL_DO_NOT_SEND", "PRIVATE_LIFE_DO_NOT_SEND"):
        assert private not in prompt and private not in checklist
    # The final JSON intent line remains readable by adapters and carries no private snapshot.
    assert json.loads(prompt.rsplit("\n", 1)[1])["ticker"] == "NVDA"


def test_changed_snapshot_or_foreign_scope_is_rejected_without_resolving_current_policy():
    manifest = manifest_for()
    manifest["selection"]["investment_system"]["snapshot"]["policy"]["revision"] += 1
    with pytest.raises(ResearchError, match="校验失败"):
        investment_prompt(manifest)
    manifest = manifest_for()
    manifest["workspace_id"] = "another-workspace"
    with pytest.raises(ResearchError) as foreign:
        investment_prompt(manifest)
    assert foreign.value.status_code == 403


def test_runtime_resolver_uses_frozen_prompt_and_clears_host_default_when_disabled(tmp_path):
    from tradingagents.web.application.runtime import create_runtime_resolver
    from tradingagents.web.infrastructure.database import initialize_database
    from tradingagents.web.infrastructure.secrets import MemorySecretStore

    database = initialize_database(tmp_path / "app.sqlite3")
    workspace = database.local_workspace()
    resolve = create_runtime_resolver(database, {"investment_system_prompt": "UNRELATED_HOST_POLICY"}, MemorySecretStore())
    manifest = manifest_for(workspace.workspace_id, workspace.user_id)
    assert resolve(manifest)["investment_system_prompt"] == DISCIPLINE
    manifest["request"]["apply_investment_system"] = False
    assert "investment_system_prompt" not in resolve(manifest)
    database.dispose()


def test_builtin_worker_injects_frozen_policy_and_exports_the_deterministic_checklist(tmp_path):
    from tradingagents.reporting import write_report_tree
    from tradingagents.web.application.research import ResearchService
    from tradingagents.web.infrastructure.database import initialize_database
    from tradingagents.web.workers.research import ResearchWorker

    database = initialize_database(tmp_path / "app.sqlite3")
    workspace = database.local_workspace()
    scope = {"workspace_id": workspace.workspace_id, "owner_id": workspace.user_id}
    service = ResearchService(database, tmp_path / "artifacts")
    selected = manifest_for(**scope)["selection"]
    queued = service.create_run({"ticker": "NVDA", "date": "2024-01-02", "apply_investment_system": True,
        "checkpoint": False}, manifest=selected, **scope)
    # A later edit cannot change the already persisted run's input.
    selected["investment_system"]["prompt"] = "NEW_CURRENT_POLICY_MUST_NOT_REPLACE_FROZEN"
    seen = []

    class Graph:
        def __init__(self, analysts, config, callbacks):
            self.config = config
            self.resolve_instrument_context = lambda *_: "Identity."
            self.propagator = Propagator()
            self.propagator.get_graph_args = lambda **_kwargs: {}
            seen.append(config)

        def create_run_state(self, *args):
            state = TradingAgentsGraph.create_run_state(self, *args)
            assert DISCIPLINE in state["instrument_context"]
            return state

        def begin_checkpoint(self, *_args):
            return None

        def checkpoint_input(self, state):
            return state

        def stream_run(self, *_args, **_kwargs):
            yield [], {"final_trade_decision": "Final Rating: Hold", "final_rating": "Hold"}

        def save_reports(self, state, ticker, save_path, html):
            write_report_tree(state, ticker, save_path, html=html)

        def record_decision(self, *_args):
            pass

        def clear_checkpoint_on_success(self, *_args):
            pass

        def end_checkpoint(self):
            pass

    worker = ResearchWorker(service, lambda _: {"investment_system_prompt": "HOST_POLICY_NOT_ALLOWED"}, graph_factory=Graph)
    assert worker.run_once()
    result = service.get_run(queued["id"], **scope)
    assert result["status"] == "completed", result
    assert result["quality"]["eligible_for_rating"] is False
    assert seen[0]["investment_system_prompt"] == DISCIPLINE
    assert "investment_system_checklist" in result["reports"]
    assert "版本 3" in result["reports"]["investment_system_checklist"]
    for kind in ("html", "md"):
        exported = service.report_file(queued["id"], kind, **scope).read_text()
        assert "投资体系与决策核对" in exported and "PRIVATE_CAPITAL_DO_NOT_SEND" not in exported
    frozen = service.get_manifest(queued["id"], **scope)
    assert frozen["selection"]["investment_system"]["prompt"] == DISCIPLINE
    database.dispose()
