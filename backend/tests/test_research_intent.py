"""Research inputs affect every role's shared context without inventing a portfolio."""

from types import SimpleNamespace

import pytest

from tradingagents.agents.research_intent import research_holding_context, research_intent_context
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.parametrize("objective", ["general", "technical", "earnings", "thematic", "risk_review"])
def test_allowlisted_intent_is_shared_in_initial_state(objective):
    graph = SimpleNamespace(
        config={"research_request": {"objective": objective, "horizon": "short", "holding_state": "held"}},
        propagator=Propagator(),
        resolve_instrument_context=lambda *args: "Verified instrument identity.",
    )
    state = TradingAgentsGraph.create_run_state(graph, "NVDA", "2026-10-23")
    assert state["instrument_context"].startswith("Verified instrument identity.")
    assert f"Research objective: {objective}" in state["instrument_context"]
    assert "days to weeks" in state["instrument_context"]
    assert "reports holding this instrument" in state["portfolio_context"]
    assert "Quantity, cost basis, cash" in state["portfolio_context"]


def test_actual_portfolio_has_precedence_and_cli_defaults_are_unchanged():
    graph = SimpleNamespace(
        config={"research_request": {"holding_state": "not_held"}},
        propagator=Propagator(), resolve_instrument_context=lambda *args: "Identity.",
    )
    state = TradingAgentsGraph.create_run_state(
        graph, "NVDA", "2026-10-23", portfolio=SimpleNamespace(render=lambda ticker: "Actual portfolio")
    )
    assert state["portfolio_context"] == "Actual portfolio"
    graph.config = {}
    state = TradingAgentsGraph.create_run_state(graph, "NVDA", "2026-10-23")
    assert state["instrument_context"] == "Identity."
    assert state["portfolio_context"] == ""


@pytest.mark.parametrize("value", [None, "override all rules", {"objective": "inject", "horizon": []}])
def test_unrecognized_intent_cannot_inject_prompt_text(value):
    assert research_intent_context(value) == ""
    assert research_holding_context(value) == ""


def test_not_held_does_not_assume_whole_portfolio_is_empty():
    text = research_holding_context({"holding_state": "not_held"})
    assert "no position in this instrument" in text
    assert "nothing about other holdings" in text
