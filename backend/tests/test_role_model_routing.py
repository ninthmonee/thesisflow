"""Role models reach the existing graph and invalidate incompatible checkpoints."""

from copy import deepcopy

import pytest

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import setup, trading_graph
from tradingagents.graph.conditional_logic import ConditionalLogic

FACTORIES = {
    "create_market_analyst": "market",
    "create_sentiment_analyst": "social",
    "create_news_analyst": "news",
    "create_fundamentals_analyst": "fundamentals",
    "create_bull_researcher": "bull",
    "create_bear_researcher": "bear",
    "create_research_manager": "research_manager",
    "create_trader": "trader",
    "create_aggressive_debator": "risk_aggressive",
    "create_neutral_debator": "risk_neutral",
    "create_conservative_debator": "risk_conservative",
    "create_portfolio_manager": "portfolio_manager",
}


def capture_factories(monkeypatch):
    captured = {}
    for name, role in FACTORIES.items():
        def factory(model, role=role):
            captured[role] = model
            return lambda state: {}
        monkeypatch.setattr(setup, name, factory)
    return captured


def test_each_role_receives_its_resolved_model(monkeypatch):
    captured = capture_factories(monkeypatch)
    models = {role: object() for role in FACTORIES.values()}
    builder = setup.GraphSetup(object(), object(), ConditionalLogic(), 2,
                               role_model_resolver=models.__getitem__)
    builder.setup_graph()
    assert captured == models


def test_original_quick_deep_defaults_remain_unchanged(monkeypatch):
    captured = capture_factories(monkeypatch)
    quick, deep = object(), object()
    setup.GraphSetup(quick, deep, ConditionalLogic(), 2).setup_graph()
    for role, model in captured.items():
        assert model is (deep if role in {"research_manager", "portfolio_manager"} else quick)


def test_resolved_graph_does_not_construct_unconfigured_default_clients(tmp_path, monkeypatch):
    captured = capture_factories(monkeypatch)
    models = {role: object() for role in [*FACTORIES.values(), "quick", "deep"]}

    def unexpected_client(*args, **kwargs):
        raise AssertionError("A resolved graph must not load an unrelated environment client")

    monkeypatch.setattr(trading_graph, "create_tier_client", unexpected_client)
    config = deepcopy(DEFAULT_CONFIG)
    config.update(data_cache_dir=str(tmp_path / "cache"), results_dir=str(tmp_path / "results"),
                  memory_log_path=str(tmp_path / "memory.md"))
    graph = trading_graph.TradingAgentsGraph(
        config=config, role_model_resolver=models.__getitem__, role_model_signature="profile-v1",
    )
    assert graph.quick_thinking_llm is models["quick"]
    assert graph.deep_thinking_llm is models["deep"]
    assert captured == {role: models[role] for role in FACTORIES.values()}
    before = graph._run_signature("stock")
    graph.role_model_signature = "profile-v2"
    assert graph._run_signature("stock") != before


def test_resolver_requires_explicit_checkpoint_identity():
    with pytest.raises(ValueError, match="role_model_signature"):
        trading_graph.TradingAgentsGraph(role_model_resolver=lambda _role: object())
