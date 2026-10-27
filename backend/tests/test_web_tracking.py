"""Tracking observes owned securities and exposes reproducible, quality-gated daily rules."""

from concurrent.futures import ThreadPoolExecutor, wait as original_wait
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, RLock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.web.api.tracking_router import create_tracking_router
from tradingagents.web.api.tracking_schemas import TrackingError, WatchlistTrackingItem
from tradingagents.web.application import tracking as tracking_module
from tradingagents.web.application.tracking import WatchlistTrackingService
from tradingagents.web.domain.tracking import (
    analyze_tracking,
    closed_sessions,
    volatility_level,
    wilder_atr,
)
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import (
    Instrument,
    Membership,
    User,
    Watchlist,
    WatchlistItem,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def history(now=NOW, *, symbol="AAA", direction=1, count=65):
    days = closed_sessions(now)[-count:]
    bars = []
    for index, day in enumerate(days):
        close = (
            100 + index * 0.5 if direction == 1 else 200 - index * 0.5 if direction == -1 else 100
        )
        bars.append(
            {
                "date": day,
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000 + index,
                "session": "regular",
                "is_complete": True,
            }
        )
    return {
        "symbol": symbol,
        "interval": "1d",
        "session": "regular",
        "adjustment": "provider_native",
        "bars": bars,
        "meta": {
            "source": "fixture-provider",
            "feed": "fixture-daily",
            "quality": "complete",
            "cache": "fresh",
            "received_at": now.isoformat(),
            "coverage": {"dropped_count": 0, "duplicate_count": 0, "calendar_verified": True},
        },
    }


def item(analysis):
    return WatchlistTrackingItem(
        instrument_id="fixture",
        symbol="AAA",
        name="AAA",
        asset_type="stock",
        currency="USD",
        **analysis,
    )


def test_upward_and_downward_scenarios_use_close_anchor_and_exact_atr_multiples():
    for direction in (1, -1):
        result = analyze_tracking(history(direction=direction), NOW)
        assert result["meta"].quality == "complete" and result["eligible_for_levels"]
        assert result["trend"] == ("uptrend" if direction == 1 else "downtrend")
        assert result["observation"] == ("above_range" if direction == 1 else "below_range")
        assert result["atr14"] == pytest.approx(0.7)
        assert result["reference_target"] == pytest.approx(
            result["price"] + direction * 2 * result["atr14"]
        )
        assert result["protection_level"] == pytest.approx(
            result["price"] - direction * 1.5 * result["atr14"]
        )
        assert result["price"] != result["previous_close"] and result["change_percent"] is not None
        item(result)


def test_wilder_seed_uses_14_trs_and_exactly_64_updates_from_65_rows():
    # TR is 1..64. Closed-form ATR at n=64 is 64-13 + 6.5*(13/14)**50.
    rows = [{"open": 100, "close": 100, "low": 100, "high": 100}]
    rows += [{"open": 100, "close": 100, "low": 100, "high": 100 + span} for span in range(1, 65)]
    assert wilder_atr(rows) == pytest.approx(51 + 6.5 * (13 / 14) ** 50)
    assert wilder_atr(rows[:14]) is None


@pytest.mark.parametrize(
    ("value", "level"), [(0, 1), (0.999, 1), (1, 2), (2, 3), (3, 4), (5, 5), (100, 5), (None, None)]
)
def test_technical_volatility_threshold_boundaries(value, level):
    assert volatility_level(value) == level


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_ohlc_never_generates_levels_or_zero_fills(bad):
    response = history()
    response["bars"][-3]["close"] = bad
    result = analyze_tracking(response, NOW)
    assert result["meta"].quality == "partial" and result["atr14"] is None
    assert result["reference_target"] is None and result["protection_level"] is None
    assert result["meta"].coverage.invalid_price_sessions == 1
    item(result)


def test_channel_excludes_current_day_and_incomplete_history_preserves_missing_indicators():
    response = history(count=30)
    result = analyze_tracking(response, NOW)
    assert result["ma20"] is not None and result["ma50"] is None and result["atr14"] is None
    assert result["range_high"] == max(row["high"] for row in response["bars"][-21:-1])
    assert result["range_high"] < response["bars"][-1]["high"]
    assert result["meta"].coverage.missing_sessions == 35
    assert result["reference_target"] is None and result["observation"] == "above_range"


def test_gap_duplicate_and_source_dropped_counts_block_levels_even_if_raw_claims_complete():
    for kind in ("gap", "duplicate", "reported_duplicate", "reported_drop"):
        response = history()
        if kind == "gap":
            response["bars"].pop(-25)
        elif kind == "duplicate":
            response["bars"].append(deepcopy(response["bars"][-3]))
        else:
            response["meta"]["coverage"][
                "duplicate_count" if kind == "reported_duplicate" else "dropped_count"
            ] = 1
        result = analyze_tracking(response, NOW)
        assert result["meta"].quality == "partial"
        assert not result["eligible_for_levels"] and result["reference_target"] is None


def test_stale_history_keeps_its_observed_day_and_channel_but_has_no_reference_levels():
    response = history()
    response["bars"].pop()
    result = analyze_tracking(response, NOW)
    assert result["meta"].quality == "stale"
    assert result["meta"].as_of != result["meta"].coverage.expected_latest_session
    assert result["range_high"] is not None and result["reference_target"] is None


def test_only_current_actual_unclosed_session_can_explain_source_partial():
    now = datetime(2026, 10, 26, 16, tzinfo=UTC)
    response = history(now)
    response["meta"]["quality"] = "partial"
    current = {**response["bars"][-1], "date": "2026-10-26", "is_complete": False, "close": 500}
    response["bars"].append(current)
    result = analyze_tracking(response, now)
    assert result["price"] == response["bars"][-2]["close"]
    assert result["meta"].source_quality == "partial" and result["meta"].quality == "complete"
    assert result["meta"].coverage.excluded_unclosed == 1 and result["eligible_for_levels"]
    for date, flag in (("2026-10-24", False), ("2026-10-27", False), ("2026-10-26", True)):
        corrupted = deepcopy(response)
        corrupted["bars"][-1].update(date=date, is_complete=flag)
        invalid = analyze_tracking(corrupted, now)
        assert invalid["meta"].quality == "partial" and invalid["reference_target"] is None


@pytest.mark.parametrize("stamp", [None, "2026-10-23T19:59:59Z", "2026-10-26T12:00:00Z"])
def test_received_time_must_be_known_after_as_of_close_and_not_in_future(stamp):
    response = history()
    response["meta"]["received_at"] = stamp
    result = analyze_tracking(response, NOW)
    assert result["meta"].quality == "partial" and result["reference_target"] is None


def test_exchange_early_close_is_used_instead_of_a_fixed_4pm_cutoff():
    before, after = (
        datetime(2026, 11, 27, 17, 59, tzinfo=UTC),
        datetime(2026, 11, 27, 18, 1, tzinfo=UTC),
    )
    response = history(after)
    response["meta"]["received_at"] = before.isoformat()
    response["meta"]["quality"] = "partial"
    response["bars"][-1]["is_complete"] = False
    early = analyze_tracking(response, before)
    assert early["meta"].as_of == "2026-11-25" and early["meta"].coverage.excluded_unclosed == 1
    response["meta"]["received_at"] = after.isoformat()
    response["meta"]["quality"] = "complete"
    response["bars"][-1]["is_complete"] = True
    assert analyze_tracking(response, after)["meta"].as_of == "2026-11-27"


def test_extreme_finite_numbers_do_not_crash_or_export_infinity():
    response = history()
    for row in response["bars"]:
        row.update(open=1e308, high=1.2e308, low=8e307, close=1e308)
    result = analyze_tracking(response, NOW)
    assert result["atr14"] == pytest.approx(4e307) and result["atr_percent"] == pytest.approx(40)
    item(result)
    response["bars"][-2].update(open=1e-300, high=1e-300, low=1e-300, close=1e-300)
    result = analyze_tracking(response, NOW)
    assert result["change_percent"] is None and result["meta"].quality == "partial"
    assert result["reference_target"] is None
    item(result)


def test_nonpositive_protection_and_mixed_trends_never_emit_scenario_levels():
    response = history(direction=0)
    response["bars"][-1].update(open=200, close=200, high=100000, low=0.01)
    result = analyze_tracking(response, NOW)
    assert result["observation"] == "above_range" and result["trend"] == "uptrend"
    assert result["protection_level"] is None and result["reference_target"] is None
    mixed = analyze_tracking(history(direction=0), NOW)
    assert mixed["trend"] == "mixed" and mixed["reference_target"] is None


class Market:
    def __init__(self):
        self.calls, self.lock, self.block = [], RLock(), None

    def get_bars(self, symbol, *, interval, period, session, adjustment, limit):
        assert (interval, period, session, adjustment, limit) == (
            "1d",
            "6mo",
            "regular",
            "provider_native",
            5000,
        )
        with self.lock:
            self.calls.append(symbol)
        if self.block is not None:
            self.block.wait(2)
        return history(symbol=symbol, direction=-1 if symbol == "ETF" else 1)


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "tracking.sqlite3")
    local, market, now = database.local_workspace(), Market(), [NOW]
    with database.session() as session:
        first = session.get(Watchlist, local.watchlist_id)
        second = Watchlist(
            workspace_id=local.workspace_id, owner_id=local.user_id, name="Second", position=2
        )
        stocks = [
            Instrument(symbol="AAA", name="Company A"),
            Instrument(symbol="ETF", name="US ETF", asset_type="etf"),
            Instrument(symbol="VOD.L", name="Foreign", currency="GBP"),
            Instrument(symbol="UNFOLLOWED", name="Not watched"),
        ]
        session.add_all([second, *stocks])
        session.flush()
        for group, members in ((first, stocks[:2]), (second, [stocks[0], stocks[2]])):
            for position, stock in enumerate(members):
                session.add(
                    WatchlistItem(
                        workspace_id=local.workspace_id,
                        watchlist_id=group.id,
                        instrument_id=stock.id,
                        position=position,
                    )
                )
        ids, second_id = [stock.id for stock in stocks], second.id
    service = WatchlistTrackingService(
        database, local, market, clock=lambda: now[0], timeout_seconds=0.03
    )
    yield service, database, local, market, now, ids, second_id
    if market.block is not None:
        market.block.set()
    service.close()
    database.dispose()


def test_all_groups_deduplicate_ids_and_unsupported_currency_never_fetches(context):
    service, _db, _local, market, _now, ids, second = context
    result = service.get_tracking(limit=100)
    assert result.total_count == result.returned_count == 3
    assert [row.instrument_id for row in result.items].count(ids[0]) == 1
    assert set(market.calls) == {"AAA", "ETF"}
    unsupported = next(row for row in result.items if row.symbol == "VOD.L")
    assert unsupported.price is None and unsupported.meta.quality == "unavailable"
    group = service.get_tracking(watchlist_id=second)
    assert {row.instrument_id for row in group.items} == {ids[0], ids[2]}
    assert len(market.calls) == 2


def test_selection_limit_and_owner_scope_are_checked_before_cache_or_provider(context):
    service, database, local, market, _now, ids, _second = context
    result = service.get_tracking(limit=1)
    assert result.truncated and result.total_count == 3 and result.returned_count == 1
    before = len(market.calls)
    for request, status in (
        ({"watchlist_id": "unknown"}, 404),
        ({"instrument_ids": [ids[3]]}, 404),
        ({"instrument_ids": ["not-a-uuid"]}, 422),
        ({"limit": 101}, 422),
    ):
        with pytest.raises(TrackingError) as error:
            service.get_tracking(**request)
        assert error.value.status_code == status
    with database.session() as session:
        session.add(User(id="friend", name="Friend"))
        session.flush()
        session.add(
            Membership(
                id="friend-membership",
                workspace_id=local.workspace_id,
                user_id="friend",
                role="viewer",
            )
        )
    friend = WatchlistTrackingService(
        database, replace(local, user_id="friend"), market, clock=lambda: NOW
    )
    try:
        assert friend.get_tracking().items == []
        with pytest.raises(TrackingError) as error:
            friend.get_tracking(watchlist_id=local.watchlist_id)
        assert error.value.status_code == 404
    finally:
        friend.close()
    with database.session() as session:
        membership = session.get(Membership, "friend-membership")
        session.delete(membership)
    with pytest.raises(TrackingError) as error:
        friend.rules()
    assert error.value.status_code == 403 and len(market.calls) == before


def test_explicit_refresh_is_throttled_and_never_passes_unknown_market_arguments(context):
    service, _db, _local, market, now, ids, _second = context
    service.get_tracking(instrument_ids=[ids[0]])
    now[0] += timedelta(seconds=1)
    hit = service.get_tracking(instrument_ids=[ids[0]], refresh=True)
    assert hit.refresh_throttled and hit.items[0].meta.cache == "hit" and len(market.calls) == 1
    now[0] += timedelta(seconds=30)
    fresh = service.get_tracking(instrument_ids=[ids[0]], refresh=True)
    assert (
        not fresh.refresh_throttled
        and fresh.items[0].meta.cache == "fresh"
        and len(market.calls) == 2
    )


def test_two_concurrent_first_readers_share_one_fetch_and_both_remain_fresh(context, monkeypatch):
    service, _db, _local, market, _now, ids, _second = context
    market.block, barrier = Event(), Barrier(2)

    def synchronized_wait(futures, timeout):
        barrier.wait(2)
        market.block.set()
        return original_wait(futures, timeout=2)

    monkeypatch.setattr(tracking_module, "wait", synchronized_wait)
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(service.get_tracking, instrument_ids=[ids[0]]) for _ in range(2)]
        results = [future.result(3) for future in futures]
    assert len(market.calls) == 1
    assert all(result.items[0].meta.cache in {"fresh", "hit"} for result in results)
    assert all(result.items[0].eligible_for_levels for result in results)


def test_timeout_does_not_stack_requests_and_invalidated_pending_work_cannot_repopulate_cache(
    context,
):
    service, _db, _local, market, now, ids, _second = context
    service.get_tracking(instrument_ids=[ids[0]])
    now[0] += timedelta(seconds=121)
    market.block = Event()
    stale = service.get_tracking(instrument_ids=[ids[0]])
    assert stale.items[0].meta.cache == "stale" and stale.items[0].reference_target is None
    for _ in range(3):
        service.get_tracking(instrument_ids=[ids[0]])
    assert len(service._pending) == 1 and len(market.calls) == 2
    service.invalidate()
    assert service._cache == {}
    market.block.set()
    original_wait([entry[1] for entry in service._pending.values()], timeout=2)
    fresh = service.get_tracking(instrument_ids=[ids[0]])
    assert len(market.calls) == 3 and fresh.items[0].meta.cache == "fresh"


def test_router_validates_queries_and_returns_public_rules_without_model_requests(context):
    service, _db, _local, market, _now, ids, _second = context
    app = FastAPI()
    app.include_router(create_tracking_router(lambda: service))
    with TestClient(app) as client:
        rules = client.get("/api/v1/watchlist-tracking/rules")
        assert rules.status_code == 200 and rules.json()["configurable"] is False
        assert any("0.5ATR" in row["description"] for row in rules.json()["formulas"])
        assert market.calls == []
        assert (
            client.get("/api/v1/watchlist-tracking", params={"watchlist_id": "unknown"}).status_code
            == 404
        )
        assert (
            client.get(
                "/api/v1/watchlist-tracking", params={"instrument_ids": "bad,,ids"}
            ).status_code
            == 422
        )
        assert client.get("/api/v1/watchlist-tracking", params={"limit": 101}).status_code == 422
        assert (
            client.get("/api/v1/watchlist-tracking", params={"refresh": "maybe"}).status_code == 422
        )
        valid = client.get("/api/v1/watchlist-tracking", params={"instrument_ids": ids[0]})
        assert (
            valid.status_code == 200
            and valid.json()["items"][0]["price_kind"] == "completed_daily_close"
        )
