"""Market brief models select grounded facts; they cannot author new numbers."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tradingagents.web.api.intelligence_schemas import IntelligenceError, MarketSnapshot
from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.application.market_briefs import MarketBriefGenerator


def snapshot():
    facts = [
        {"id": "spy-return", "label": "SPY收益", "value": 1.25, "unit": "%"},
        {"id": "vix-level", "label": "VIX", "value": 17.4, "unit": "points"},
        {"id": "xlk-relative", "label": "XLK相对SPY", "value": 2.5, "unit": "pp"},
        {"id": "xlk-rvol", "label": "XLK RVOL", "value": 1.7, "unit": "times"},
        {"id": "breadth", "label": "样本覆盖", "value": 30, "unit": "stocks"},
    ]
    for row in facts:
        row.update(as_of="2026-10-23", source="fixture", feed="fixture-bars", quality="complete", method="fixture")
    return MarketSnapshot.model_validate({
        "id": "mkt-" + "a" * 32, "window": "1D", "window_start": "2026-10-22", "window_end": "2026-10-23",
        "as_of": "2026-10-23", "received_at": "2026-10-24T01:00:00Z", "source": "fixture", "feed": "fixture-bars",
        "quality": "complete", "benchmarks": [
            {"symbol": "SPY", "name": "标普代理", "kind": "etf_proxy", "price": 100, "return_percent": 1.25,
             "as_of": "2026-10-23", "quality": "complete", "evidence_ids": ["spy-return"]},
            {"symbol": "^VIX", "name": "VIX", "kind": "volatility_index", "price": 17.4, "return_percent": None,
             "as_of": "2026-10-23", "quality": "complete", "evidence_ids": ["vix-level"]}],
        "sectors": [{"symbol": "XLK", "name": "科技", "return_percent": 3.75, "relative_return_pp": 2.5,
                     "volume": 100, "turnover_estimate_usd": 10000, "daily_rvol": 1.7, "rvol_sample_count": 20,
                     "as_of": "2026-10-23", "quality": "complete", "evidence_ids": ["xlk-relative", "xlk-rvol"]}],
        "breadth": {"universe_name": "观察样本", "universe_version": "1", "universe_symbols": ["AAPL"],
                    "requested_count": 30, "available_count": 30, "advancers": 18, "decliners": 12,
                    "unchanged": 0, "above_sma20_percent": 60, "sma20_sample_count": 30,
                    "quality": "complete", "evidence_ids": ["breadth"]},
        "evidence": facts, "observed_divergences": [], "optional_data": [
            {"id": "etf_flows", "name": "ETF净申赎", "reason": "未接入可靠来源"}], "warnings": [],
    }).model_dump(mode="json")


class ChoosingModel:
    def __init__(self, selection=None):
        self.selection = selection or {"state": "state:breadth", "changes": ["change:SPY"],
                                       "attention": ["attention:XLK"], "risks": ["risk:flows"]}
        self.calls = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.calls.append(prompt)
        return self.selection


def test_brief_is_on_demand_and_numbers_are_from_snapshot_only():
    model = ChoosingModel()
    generator = MarketBriefGenerator(Mock(), model_loader=lambda: (model, "deepseek", "configured-model"))
    assert model.calls == []
    result = generator(snapshot(), "Chinese")
    assert len(model.calls) == 1
    assert result.provider == "deepseek" and result.model == "configured-model"
    assert "2.50" in result.content.attention[0].text and "1.70" in result.content.attention[0].text
    assert result.content.attention[0].evidence_ids == ["xlk-relative", "xlk-rvol"]
    assert "不能用作净流入" in result.content.risks[0].text
    assert "PRIVATE" not in str(model.calls)


@pytest.mark.parametrize("bad", [
    {"state": "outside-snapshot"},
    {"state": "attention:XLK"},
    {"state": "state:scope", "attention": ["attention:invented"]},
    {"state": "state:scope", "text": "机构净买入9999亿美元"},
])
def test_model_cannot_add_unknown_facts_or_free_text(bad):
    generator = MarketBriefGenerator(Mock(), model_loader=lambda: (ChoosingModel(bad), "test", "test"))
    with pytest.raises(IntelligenceError) as error:
        generator(snapshot(), "Chinese")
    assert error.value.status_code == 422


def test_plain_json_fallback_is_validated_and_failure_never_leaks_details():
    model = Mock()
    model.with_structured_output.side_effect = NotImplementedError()
    model.invoke.return_value = SimpleNamespace(content=json.dumps({"state": "state:scope"}))
    generator = MarketBriefGenerator(Mock(), model_loader=lambda: (model, "compatible", "local-model"))
    assert generator(snapshot(), "English").content.state.text.startswith("Observed window")
    model.invoke.side_effect = RuntimeError("private-key https://private.invalid")
    with pytest.raises(IntelligenceError) as error:
        generator(snapshot(), "English")
    assert error.value.status_code == 503
    assert "private-key" not in str(error.value) and "private.invalid" not in str(error.value)


def test_configured_brief_uses_only_tested_default_quick_profile(monkeypatch):
    service = Mock()
    service.settings.return_value = {"default_template": {"default_profile_id": "default-profile"}}
    service.resolve_profile_snapshot.return_value = {
        "provider": "anthropic", "model": "actual-model", "workspace_id": "space", "profile_id": "default-profile"}
    service.workspace = SimpleNamespace(workspace_id="space", user_id="user")
    model = ChoosingModel()
    runtime = Mock(return_value=lambda manifest: {"__role_model_resolver": lambda role: model})
    monkeypatch.setattr("tradingagents.web.application.market_briefs.create_runtime_resolver", runtime)
    generator = MarketBriefGenerator(service, local_status_reader=lambda: {"model_ready": False})
    result = generator(snapshot())
    assert result.provider == "anthropic" and result.model == "actual-model"
    service.resolve_profile_snapshot.assert_called_once_with("default-profile", require_tested=True)
    service.resolve_profile_snapshot.side_effect = ModelConfigurationError("model_not_tested", "默认模型未检测", 409)
    with pytest.raises(IntelligenceError) as error:
        generator(snapshot())
    assert error.value.code == "model_not_tested"


def test_local_auth_and_missing_evidence_are_checked_before_calling_model():
    service = Mock()
    service.settings.return_value = {"default_template": None}
    generator = MarketBriefGenerator(service, local_status_reader=lambda: {"model_ready": False})
    with pytest.raises(IntelligenceError) as error:
        generator(snapshot())
    assert error.value.code == "market_model_required"
    loader = Mock()
    generated = MarketBriefGenerator(service, model_loader=loader)
    empty = snapshot()
    for row in empty["evidence"]:
        row["value"] = None
    with pytest.raises(IntelligenceError) as error:
        generated(empty)
    assert error.value.code == "market_data_insufficient" and loader.call_count == 0
