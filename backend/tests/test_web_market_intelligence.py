"""Market comparisons use real-shaped fixtures with strict time/feed boundaries."""

import json
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime

import exchange_calendars
import pytest

from tradingagents.web.api.intelligence_schemas import BriefGeneration, IntelligenceError
from tradingagents.web.application.market_intelligence import (
    OBSERVATION_SYMBOLS,
    MarketIntelligenceService,
)

NOW = datetime(2024, 4, 1, 15, tzinfo=UTC)


def daily_response(symbol):
    exchange = exchange_calendars.get_calendar("XNYS")
    days = exchange.sessions_in_range("2023-12-01", "2024-03-28")
    bars = [
        {
            "date": day.strftime("%Y-%m-%d"),
            "timestamp": exchange.session_open(day).isoformat(),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 100,
            "session": "regular",
            "is_complete": True,
        }
        for index, day in enumerate(days)
    ]
    bars[-1]["volume"] = 200
    return {
        "symbol": symbol,
        "interval": "1d",
        "period": "3mo",
        "session": "regular",
        "adjustment": "provider_native",
        "bars": bars,
        "meta": {
            "source": "yahoo",
            "feed": "yahoo_chart",
            "quality": "complete",
            "received_at": NOW.isoformat(),
            "as_of": bars[-1]["timestamp"],
            "coverage": {"calendar_verified": not symbol.startswith("^")},
        },
    }


class FixtureData:
    def __init__(self):
        self.values = {}
        self.calls = 0
        self.active = self.maximum_active = 0
        self.lock = threading.Lock()
        self.delay = 0

    def get_bars(self, symbol, **kwargs):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            value = self.values.get(symbol)
            if isinstance(value, Exception):
                raise value
            return deepcopy(value if value is not None else daily_response(symbol))
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def market(tmp_path):
    data = FixtureData()
    service = MarketIntelligenceService(data, tmp_path / "snapshots", clock=lambda: NOW)
    yield service, data
    service.close()


def test_relative_returns_and_daily_rvol_share_feed_and_cutoff(market):
    service, data = market
    special = daily_response("XLK")
    special["bars"][-1]["close"] = special["bars"][-2]["close"] * 1.1
    data.values["XLK"] = special
    snapshot = service.get_snapshot("1D")
    spy = next(row for row in snapshot.benchmarks if row.symbol == "SPY")
    technology = next(row for row in snapshot.sectors if row.symbol == "XLK")
    assert snapshot.window_start == "2024-03-27"
    assert snapshot.window_end == snapshot.as_of == "2024-03-28"
    assert technology.return_percent == pytest.approx(10)
    assert technology.relative_return_pp == pytest.approx(10 - spy.return_percent, abs=0.00001)
    assert technology.daily_rvol == 2
    assert technology.rvol_sample_count == 20
    assert technology.turnover_estimate_usd == special["bars"][-1]["close"] * 200
    assert "估算" in technology.turnover_method and "不代表净流入" in technology.turnover_method
    assert len(snapshot.sectors) == 11
    assert snapshot.breadth.available_count == snapshot.breadth.requested_count == 30
    assert snapshot.breadth.advancers == 30
    assert "非全市场" in snapshot.breadth.scope_notice
    assert (
        next(row for row in snapshot.benchmarks if row.symbol == "^VIX").kind == "volatility_index"
    )
    assert all(row.state == "unavailable" for row in snapshot.optional_data)


def test_uncompleted_daily_bar_never_uses_partial_volume_against_full_days(market):
    service, data = market
    for symbol in ("SPY", "XLK"):
        response = daily_response(symbol)
        response["bars"].append(
            {
                "date": "2024-04-01",
                "timestamp": "2024-04-01T13:30:00+00:00",
                "open": 1000,
                "high": 2000,
                "low": 999,
                "close": 1999,
                "volume": 9999999,
                "session": "regular",
                "is_complete": False,
            }
        )
        response["meta"]["quality"] = "partial"
        data.values[symbol] = response
    snapshot = service.get_snapshot()
    sector = next(row for row in snapshot.sectors if row.symbol == "XLK")
    assert snapshot.as_of == "2024-03-28"
    assert sector.volume == 200 and sector.daily_rvol == 2
    assert snapshot.quality == "partial"


def test_cross_feed_and_missing_constituents_are_excluded_with_coverage(market):
    service, data = market
    mismatched = daily_response("XLK")
    mismatched["meta"]["feed"] = "different-venue"
    data.values["XLK"] = mismatched
    data.values["AAPL"] = RuntimeError("private transport details")
    snapshot = service.get_snapshot()
    sector = next(row for row in snapshot.sectors if row.symbol == "XLK")
    assert sector.relative_return_pp is None and sector.daily_rvol is None
    assert "feed不一致" in sector.reason
    assert snapshot.breadth.available_count == 29 and snapshot.breadth.advancers == 29
    assert snapshot.quality == "partial"
    assert "private transport" not in snapshot.model_dump_json()


def test_no_breadth_data_is_null_not_a_zero_sentiment_reading(market):
    service, data = market
    for symbol in OBSERVATION_SYMBOLS:
        data.values[symbol] = RuntimeError("not available")
    snapshot = service.get_snapshot()
    assert snapshot.breadth.available_count == 0
    assert snapshot.breadth.advancers is None and snapshot.breadth.decliners is None
    assert snapshot.breadth.above_sma20_percent is None


def test_calendar_gap_is_not_silently_replaced_by_an_older_bar(market):
    service, data = market
    response = daily_response("SPY")
    response["bars"] = [row for row in response["bars"] if row["date"] != "2024-03-27"]
    data.values["SPY"] = response
    snapshot = service.get_snapshot("1D")
    assert snapshot.window_start == "2024-03-27"
    assert snapshot.quality == "unavailable"
    assert next(row for row in snapshot.benchmarks if row.symbol == "SPY").return_percent is None


def test_five_day_and_calendar_month_windows_are_explicit(market):
    service, _data = market
    five = service.get_snapshot("5D")
    month = service.get_snapshot("1M")
    assert five.window_start == "2024-03-21" and five.window_end == "2024-03-28"
    assert month.window_start == "2024-02-28" and month.window_end == "2024-03-28"


def test_insufficient_volume_history_keeps_rvol_unavailable(market):
    service, data = market
    response = daily_response("XLK")
    response["bars"] = response["bars"][-10:]
    data.values["XLK"] = response
    snapshot = service.get_snapshot()
    sector = next(row for row in snapshot.sectors if row.symbol == "XLK")
    assert sector.daily_rvol is None and sector.rvol_sample_count == 9
    assert sector.volume == 200


def test_snapshot_cache_is_immutable_and_does_not_call_models(market):
    service, data = market
    model_calls = []
    service.brief_generator = lambda *args: model_calls.append(args)
    first = service.get_snapshot()
    calls = data.calls
    first.sectors[0].return_percent = 99999
    second = service.get_snapshot()
    assert second.sectors[0].return_percent != 99999
    assert data.calls == calls
    assert model_calls == []


def test_disk_snapshot_is_validated_after_service_restart(market, tmp_path):
    service, _data = market
    snapshot = service.get_snapshot()
    restored = MarketIntelligenceService(FixtureData(), tmp_path / "snapshots", clock=lambda: NOW)
    try:
        assert restored.get_cached_snapshot(snapshot.id).model_dump() == snapshot.model_dump()
        path = tmp_path / "snapshots" / f"{snapshot.id}.json"
        body = json.loads(path.read_text())
        body["benchmarks"][0]["price"] = 999
        path.write_text(json.dumps(body))
        restored._snapshots.clear()
        with pytest.raises(IntelligenceError) as captured:
            restored.get_cached_snapshot(snapshot.id)
        assert captured.value.code == "market_snapshot_not_found"
    finally:
        restored.close()


def test_brief_uses_frozen_snapshot_and_validates_evidence(market):
    service, data = market
    for symbol in OBSERVATION_SYMBOLS[:20]:
        response = daily_response(symbol)
        response["bars"][-1]["close"] = response["bars"][-2]["close"] * 0.95
        data.values[symbol] = response
    snapshot = service.get_snapshot()
    assert snapshot.observed_divergences
    seen = []

    def generator(document, language):
        seen.append((document, language))
        return BriefGeneration.model_validate(
            {
                "model": "configured-model",
                "provider": "configured-provider",
                "content": {
                    "state": {
                        "text": "指数与样本广度分歧",
                        "evidence_ids": ["benchmark:SPY:return", "breadth:decliners"],
                    },
                    "attention": [
                        {"text": "行业相对收益", "evidence_ids": ["sector:XLK:relative"]}
                    ],
                },
            }
        )

    brief = service.generate_brief(snapshot.id, generator=generator)
    assert brief.snapshot_id == snapshot.id and brief.as_of == snapshot.as_of
    assert brief.model == "configured-model"
    assert brief.observed_divergences == snapshot.observed_divergences
    assert any("净申赎" in gap for gap in brief.data_gaps)
    assert seen[0][0]["id"] == snapshot.id and seen[0][1] == "Chinese"
    with pytest.raises(IntelligenceError) as captured:
        service.generate_brief(
            snapshot.id,
            generator=lambda *_: {"state": {"text": "fabricated", "evidence_ids": ["unknown"]}},
        )
    assert captured.value.code == "invalid_brief_evidence"


def test_brief_failure_is_safe_and_unconfigured_generator_is_explicit(market):
    service, _data = market
    snapshot = service.get_snapshot()
    with pytest.raises(IntelligenceError) as captured:
        service.generate_brief(snapshot.id)
    assert captured.value.code == "market_model_required"

    def broken(*args):
        raise RuntimeError("sk-secret Authorization path")

    with pytest.raises(IntelligenceError) as captured:
        service.generate_brief(snapshot.id, generator=broken)
    assert "sk-secret" not in captured.value.message


def test_aggregation_uses_bounded_independent_data_pool(tmp_path):
    data = FixtureData()
    data.delay = 0.005
    service = MarketIntelligenceService(
        data, clock=lambda: NOW, max_workers=3, fetch_timeout_seconds=30
    )
    try:
        service.get_snapshot()
        assert 1 < data.maximum_active <= 3
    finally:
        service.close()


def test_reconfigure_defers_pool_replacement_until_pending_requests_finish(market):
    service, _data = market
    release = threading.Event()
    pending = service._executor.submit(lambda: release.wait(5))
    service._pending[pending] = "SPY"
    previous = service._executor
    try:
        assert not service.configure(max_workers=2, fetch_timeout_seconds=10, cache_seconds=300)
        assert service._executor is previous and service.max_workers == 4
        release.set()
        pending.result(timeout=1)
        assert service.configure(max_workers=2, fetch_timeout_seconds=10, cache_seconds=300)
        assert service._executor is not previous and service.max_workers == 2
        assert service.cache_seconds == 300 and service.fetch_timeout_seconds == 10
    finally:
        release.set()


def test_cache_invalidation_retains_frozen_snapshot_evidence(market):
    service, data = market
    snapshot = service.get_snapshot()
    calls = data.calls
    assert service.invalidate_market_data_cache()
    assert service.get_cached_snapshot(snapshot.id) == snapshot
    assert not service._latest and not service._responses
    service.get_snapshot()
    assert data.calls > calls


def test_configuration_and_invalidation_wait_for_active_batch_boundary(market):
    service, _data = market
    snapshot = service.get_snapshot()
    original_pool = service._executor
    assert service._build_lock.acquire(blocking=False)
    try:
        assert not service.configure(max_workers=1, fetch_timeout_seconds=20, cache_seconds=600)
        assert not service.invalidate_market_data_cache()
        assert service._executor is original_pool
        assert service.get_cached_snapshot(snapshot.id) == snapshot
    finally:
        service._build_lock.release()
    refreshed = service.get_snapshot()
    assert service.max_workers == 1 and service._executor is not original_pool
    assert refreshed.as_of == snapshot.as_of
    assert service.get_cached_snapshot(snapshot.id) == snapshot


def test_rvol_and_moving_average_do_not_skip_missing_history_sessions(market):
    service, data = market
    for symbol in ("XLK", "AAPL"):
        response = daily_response(symbol)
        del response["bars"][-5]
        data.values[symbol] = response
    snapshot = service.get_snapshot()
    sector = next(row for row in snapshot.sectors if row.symbol == "XLK")
    assert sector.daily_rvol is None and sector.rvol_sample_count == 19
    assert snapshot.breadth.available_count == 30
    assert snapshot.breadth.sma20_sample_count == 29
