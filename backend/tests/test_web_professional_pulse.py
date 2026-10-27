"""Explicit professional selections, comparable local baselines and bounded scopes."""

import json
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.web.api.professional_pulse_router import create_professional_pulse_router
from tradingagents.web.application.market_intelligence import ProfessionalMarketPulseService


class Sources:
    workspace_id, owner_id = "workspace-a", "owner-a"

    def __init__(self):
        self.calls = []
        self.connections = []
        self.as_of = "2026-10-23"
        self.ratio, self.flow, self.balance = 1.2, 100.0, 0.2
        self.contracts = ["NVDA-C", "NVDA-P"]

    def list_connections(self):
        return self.connections

    def connect(self, provider, enabled=True):
        self.connections.append(
            SimpleNamespace(
                id=f"connection-{provider}",
                revision=1,
                feed="indicative",
                provider=provider,
                enabled=enabled,
                credential_configured=True,
                secondary_credential_configured=True,
                created_at=datetime(2026, 10, 24, tzinfo=UTC),
            )
        )

    def meta(self, provider):
        return {
            "source": provider,
            "feed": "fixture-feed",
            "as_of": self.as_of,
            "received_at": "2026-10-25T12:00:00Z",
            "quality": "partial",
            "coverage": {"sample_count": 2},
            "scope": "returned sample",
            "warnings": [],
        }

    def get_options(self, symbol, **kwargs):
        self.calls.append(("options", symbol, kwargs))
        return {
            "contracts": [{"symbol": value} for value in self.contracts],
            "selected_expirations": ["2026-10-30"],
            "put_call_volume_ratio": self.ratio,
            "put_call_oi_ratio": None,
            "meta": self.meta(kwargs["provider"]),
        }

    def get_etf_flows(self, symbols, **kwargs):
        self.calls.append(("flows", symbols, kwargs))
        return {
            "funds": [
                {
                    "symbol": symbols[0],
                    "points": [
                        {
                            "as_of_date": self.as_of,
                            "net_flow_usd": self.flow,
                            "flow_kind": "estimated",
                        }
                    ],
                    "meta": self.meta(kwargs["provider"]),
                }
            ]
        }

    def get_news_sentiment(self, **kwargs):
        self.calls.append(("news", kwargs))
        return {
            "article_count": 3,
            "publisher_count": 2,
            "ticker_count": 4,
            "positive": 3,
            "negative": 2,
            "neutral": 0,
            "unclassified_articles": 1,
            "sentiment_balance": self.balance,
            "meta": self.meta(kwargs["provider"]),
        }


@pytest.fixture
def service(tmp_path):
    sources = Sources()
    now = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    instance = ProfessionalMarketPulseService(sources, tmp_path, clock=lambda: now[0])
    yield instance, sources, now
    instance.close()


def test_empty_or_news_not_opted_in_never_requests_sources(service):
    pulse, sources, _now = service
    result = pulse.get_pulse(news_symbol="NVDA", include_news=False)
    assert sources.calls == []
    assert (
        result.options_availability
        == result.flows_availability
        == result.news_availability
        == "disabled"
    )
    assert result.news is None and result.options == result.etf_flows == []


def test_missing_and_explicitly_disabled_connections_do_not_invoke_adapter(service):
    pulse, sources, _now = service
    sources.connect("massive", enabled=False)
    result = pulse.get_pulse(
        options_symbols=["NVDA"],
        options_provider="massive",
        etf_symbols=["SOXX"],
        flows_provider="intrinio",
        include_news=True,
    )
    assert sources.calls == []
    assert (
        result.options_availability
        == result.flows_availability
        == result.news_availability
        == "configuration_required"
    )
    assert result.options[0].contract_count is None and result.etf_flows[0].point_count is None
    assert result.news.article_count is None and result.news.sentiment_balance is None
    assert all(value is None for value in result.news.meta.baseline.deltas.values())


def test_explicit_free_and_existing_account_samples_remain_independent(service):
    pulse, sources, _now = service
    sources.connect("massive")
    result = pulse.get_pulse(
        options_symbols=["NVDA", "NVDA"], etf_symbols=["SOXX"], include_news=True
    )
    assert len(sources.calls) == 3
    assert (
        result.options[0].put_call_volume_ratio == 1.2
        and result.options[0].put_call_oi_ratio is None
    )
    assert result.etf_flows[0].flow_kind == "estimated"
    assert result.etf_flows[0].latest_flow_usd == 100
    assert result.news.publisher_count == 2 and result.news.sentiment_balance == 0.2
    assert result.options[0].meta.baseline.status == result.news.meta.baseline.status == "unknown"
    assert "全市场" in result.scope_notice and "不是净买入" in result.options[0].meta.method


def test_baseline_excludes_same_asof_then_compares_only_available_scalars(service):
    pulse, sources, now = service
    first = pulse.get_pulse(options_symbols=["NVDA"])
    sources.ratio = 1.5
    same = pulse.get_pulse(options_symbols=["NVDA"], refresh=True)
    assert (
        first.options[0].meta.baseline.status == same.options[0].meta.baseline.status == "unknown"
    )
    sources.as_of, sources.ratio = "2026-10-26", 2.0
    now[0] += timedelta(days=2)
    latest = pulse.get_pulse(options_symbols=["NVDA"], refresh=True)
    baseline = latest.options[0].meta.baseline
    assert baseline.status == "available" and baseline.as_of == "2026-10-23"
    assert baseline.deltas["put_call_volume_ratio"] == pytest.approx(0.5)
    assert baseline.deltas["put_call_oi_ratio"] is None


def test_member_feed_contract_and_account_revision_changes_invalidate_comparability(service):
    pulse, sources, now = service
    sources.connect("massive")
    pulse.get_pulse(options_symbols=["NVDA"], options_provider="massive")
    sources.as_of = "2026-10-26"
    now[0] += timedelta(days=2)
    sources.connections[0].revision = 2
    result = pulse.get_pulse(options_symbols=["NVDA"], options_provider="massive", refresh=True)
    assert result.options[0].meta.baseline.status == "unknown"
    sources.as_of = "2026-10-27"
    sources.contracts = ["NEW-C", "NEW-P"]
    result = pulse.get_pulse(options_symbols=["NVDA"], options_provider="massive", refresh=True)
    assert result.options[0].meta.baseline.status == "unknown"
    result = pulse.get_pulse(
        options_symbols=["NVDA", "AMD"], options_provider="massive", refresh=True
    )
    assert all(row.meta.baseline.status == "unknown" for row in result.options)


def test_unknown_latest_flow_is_not_replaced_with_old_value_or_zero(service, monkeypatch):
    pulse, sources, _now = service
    original = sources.get_etf_flows

    def flows(*args, **kwargs):
        result = original(*args, **kwargs)
        result["funds"][0]["points"].append(
            {"as_of_date": "2026-10-24", "net_flow_usd": None, "flow_kind": "estimated"}
        )
        return result

    monkeypatch.setattr(sources, "get_etf_flows", flows)
    result = pulse.get_pulse(etf_symbols=["SOXX"])
    row = result.etf_flows[0]
    assert row.point_count == 2 and row.valid_point_count == 1
    assert row.latest_flow_usd is None and row.available_points_sum_usd == 100


def test_provider_error_is_dimension_local_static_and_does_not_invent_counts(service, monkeypatch):
    pulse, sources, _now = service

    def broken(*args, **kwargs):
        raise RuntimeError("api_key=private-secret-marker contact@example.com")

    monkeypatch.setattr(sources, "get_options", broken)
    result = pulse.get_pulse(options_symbols=["NVDA"], etf_symbols=["SOXX"])
    assert result.options_availability == "error" and result.flows_availability == "available"
    assert result.options[0].contract_count is None
    assert (
        "private-secret" not in result.model_dump_json()
        and "contact@" not in result.model_dump_json()
    )


def test_timeout_is_bounded_and_pending_requests_do_not_expand_forever(tmp_path):
    sources, gate = Sources(), threading.Event()

    def blocked(*args, **kwargs):
        assert gate.wait(2)
        return {}

    sources.get_options = blocked
    pulse = ProfessionalMarketPulseService(sources, tmp_path, timeout_seconds=0.001)
    try:
        for _ in range(20):
            assert pulse.get_pulse(options_symbols=["NVDA"]).options_availability == "unavailable"
        assert len(pulse._pending) == 1
    finally:
        gate.set()
        pulse.close()


def test_history_is_owner_scoped_ttl_bounded_and_does_not_compare_corrupt_cache(service):
    pulse, sources, now = service
    raw = sources.meta("yahoo")
    for index in range(12):
        raw["as_of"] = f"2026-10-{index + 1:02}"
        pulse._baseline("same", raw, {"ratio": index})
    files = list(pulse.root.glob("*.json"))
    assert len(json.loads(files[0].read_text())) == 8
    assert files[0].stat().st_mode & 0o777 == 0o600
    now[0] += timedelta(days=36)
    raw["as_of"] = "2026-11-30"
    assert pulse._baseline("same", raw, {"ratio": 20}).status == "unknown"
    files[0].write_text("invalid-json")
    raw["as_of"] = "2026-12-01"
    assert pulse._baseline("same", raw, {"ratio": 21}).status == "unknown"
    other_sources = deepcopy(sources)
    other_sources.owner_id = "other-owner"
    other = ProfessionalMarketPulseService(
        other_sources, pulse.root.parent.parent.parent, clock=lambda: now[0]
    )
    try:
        assert other.root != pulse.root
        assert (
            other.get_pulse(options_symbols=["NVDA"]).options[0].meta.baseline.status == "unknown"
        )
    finally:
        other.close()


def test_api_selection_limits_and_stock_scope(service):
    pulse, sources, _now = service
    app = FastAPI()
    app.include_router(create_professional_pulse_router(lambda: pulse))
    with TestClient(app) as client:
        assert (
            client.get("/api/v1/market/professional-pulse").json()["news_availability"]
            == "disabled"
        )
        for query in (
            "options_symbols=NVDA.L",
            "options_symbols=EURUSD%3DX",
            "options_symbols=A,B,C,D,E,F",
            "lookback_days=91",
        ):
            assert client.get(f"/api/v1/market/professional-pulse?{query}").status_code == 422
    assert sources.calls == []
