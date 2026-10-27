"""Numeric feed data retain timezone, gaps, adjustment and provenance boundaries."""

from datetime import UTC, datetime, timedelta
from math import sqrt

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.web.api import create_api_router
from tradingagents.web.api.market_schemas import BarsResponse, MarketDataError
from tradingagents.web.application.market_data import ET, MarketDataService


class Provider:
    source = "fixture"
    feed = "fixture-bars"

    def __init__(self, frame):
        self.frame, self.calls, self.failure = frame, [], False

    def history(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        if self.failure or symbol == "MISSING":
            raise RuntimeError("private provider error https://private.invalid/key")
        result = self.frame.copy()
        if kwargs["auto_adjust"]:
            result[["Open", "High", "Low", "Close"]] *= 0.5
        return result


def frame(index):
    size = len(index)
    return pd.DataFrame(
        {
            "Open": [float(i + 10) for i in range(size)],
            "High": [float(i + 12) for i in range(size)],
            "Low": [float(i + 10) for i in range(size)],
            "Close": [float(i + 11) for i in range(size)],
            "Volume": [1000.0 + i for i in range(size)],
        },
        index=index,
    )


def test_intraday_keeps_utc_and_et_dates_across_dst_and_marks_partial(tmp_path):
    index = pd.DatetimeIndex(["2026-03-06 09:30", "2026-03-09 09:30"], tz=ET)
    provider = Provider(frame(index))
    service = MarketDataService(
        tmp_path, provider=provider, clock=lambda: datetime(2026, 3, 9, 13, 33, tzinfo=UTC)
    )
    result = service.get_bars("spy", interval="5m", period="5d")
    first, second = result["bars"]
    assert first["timestamp"].startswith("2026-03-06T14:30")
    assert second["timestamp"].startswith("2026-03-09T13:30")
    assert first["is_complete"] and not second["is_complete"]
    assert second["date"] == "2026-03-09"
    assert result["meta"]["quality"] == "partial"
    assert result["meta"]["coverage"]["available_regular_count"] == 2
    BarsResponse.model_validate(result)


def test_out_of_order_duplicate_and_missing_values_are_not_filled(tmp_path):
    index = pd.DatetimeIndex(["2026-10-23 09:35", "2026-10-23 09:30", "2026-10-23 09:30"], tz=ET)
    source = frame(index)
    source.iloc[0, source.columns.get_loc("Open")] = float("nan")
    source.iloc[0, source.columns.get_loc("Volume")] = 0
    service = MarketDataService(
        tmp_path, provider=Provider(source), clock=lambda: datetime(2026, 10, 23, 14, tzinfo=UTC)
    )
    result = service.get_bars("SPY", interval="5m", period="5d")
    assert len(result["bars"]) == 2
    assert result["bars"][0]["close"] == 13
    assert result["bars"][1]["open"] is None and result["bars"][1]["volume"] == 0
    assert result["meta"]["coverage"]["duplicate_count"] == 1
    assert result["meta"]["quality"] == "partial"


def test_invalid_price_volume_and_ohlc_ranges_are_missing_not_market_values(tmp_path):
    index = pd.date_range("2026-10-23 09:30", periods=7, freq="5min", tz=ET)
    source = pd.DataFrame(
        {
            "Open": [10, 0, 10, 20, 10, 10, 10],
            "High": [12, 12, 8, 12, 12, 12, 12],
            "Low": [9, 9, 9, 9, 9, 9, -2],
            "Close": [11, 11, 10, 10, 13, -1, 11],
            "Volume": [0, -100, 100, 100, 100, 100, 100],
        },
        index=index,
    )
    service = MarketDataService(
        tmp_path,
        provider=Provider(source),
        clock=lambda: datetime(2026, 10, 23, 14, 10, tzinfo=UTC),
    )
    result = service.get_bars("SPY", interval="5m", period="5d")
    bars = result["bars"]
    assert bars[0]["volume"] == 0 and bars[0]["close"] == 11
    assert bars[1]["open"] is None and bars[1]["volume"] is None
    assert bars[1]["close"] == 11
    for bar in bars[2:5]:
        assert all(bar[key] is None for key in ("open", "high", "low", "close"))
        assert bar["volume"] == 100
    assert bars[5]["close"] is None and bars[5]["open"] == 10
    assert bars[6]["low"] is None and bars[6]["close"] == 11
    assert result["meta"]["quality"] == "partial"
    assert "无效" in result["meta"]["warning"]
    assert service.get_bars("SPY", interval="5m", period="5d")["bars"] == bars


def test_cache_partitions_adjustment_and_stale_fallback_never_looks_fresh(tmp_path):
    now = [datetime(2026, 10, 24, 1, tzinfo=UTC)]
    provider = Provider(frame(pd.date_range("2026-10-19", "2026-10-23", freq="B", tz=ET)))
    service = MarketDataService(
        tmp_path, provider=provider, clock=lambda: now[0], cache_ttl_seconds=30
    )
    native = service.get_bars("SPY")
    hit = service.get_bars("SPY")
    adjusted = service.get_bars("SPY", adjustment="dividend_adjusted")
    assert len(provider.calls) == 2 and hit["meta"]["cache"] == "hit"
    assert adjusted["bars"][-1]["close"] == native["bars"][-1]["close"] * 0.5
    assert len(list((tmp_path / "bars").glob("*.parquet"))) == 2
    assert "不等同未复权" in native["adjustment_label"]
    now[0] += timedelta(seconds=31)
    provider.failure = True
    stale = service.get_bars("SPY")
    assert stale["meta"]["cache"] == stale["meta"]["quality"] == "stale"
    assert "private.invalid" not in str(stale) and "模拟" not in stale["meta"]["warning"]
    with pytest.raises(MarketDataError) as error:
        service.get_bars("UNAVAILABLE")
    assert error.value.status_code == 503 and "private.invalid" not in str(error.value)


def test_corrupt_cache_is_refetched_and_old_source_is_marked_stale(tmp_path):
    provider = Provider(frame(pd.date_range("2026-10-19", "2026-10-23", freq="B", tz=ET)))
    service = MarketDataService(
        tmp_path, provider=provider, clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    )
    service.get_bars("SPY")
    next((tmp_path / "bars").glob("*.parquet")).write_bytes(b"broken parquet")
    assert service.get_bars("SPY")["meta"]["cache"] == "fresh"
    old = MarketDataService(
        tmp_path / "old",
        provider=Provider(provider.frame.iloc[:1]),
        clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC),
    ).get_bars("SPY")
    assert old["meta"]["quality"] == "stale"


def test_calendar_uses_dst_holidays_and_early_close(tmp_path):
    service = MarketDataService(
        tmp_path,
        provider=Provider(pd.DataFrame()),
        clock=lambda: datetime(2026, 11, 27, 18, 5, tzinfo=UTC),
    )
    early = service.get_calendar("2026-11-26", "2026-11-27")
    assert len(early["sessions"]) == 1
    assert early["sessions"][0]["date"] == "2026-11-27"
    assert early["sessions"][0]["close"].startswith("2026-11-27T18:00")
    assert early["sessions"][0]["is_early_close"]
    dst = service.get_calendar("2026-03-06", "2026-03-09")
    assert dst["sessions"][0]["open"].startswith("2026-03-06T14:30")
    assert dst["sessions"][1]["open"].startswith("2026-03-09T13:30")
    with pytest.raises(MarketDataError):
        service.get_calendar("2026-02-30", "2026-03-01")


def test_regular_filter_uses_early_close_and_extended_preserves_post(tmp_path):
    index = pd.DatetimeIndex(["2026-11-27 12:55", "2026-11-27 13:05"], tz=ET)
    service = MarketDataService(
        tmp_path,
        provider=Provider(frame(index)),
        clock=lambda: datetime(2026, 11, 27, 18, 10, tzinfo=UTC),
    )
    regular = service.get_bars("SPY", interval="5m", period="5d")
    extended = service.get_bars("SPY", interval="5m", period="5d", session="extended")
    assert len(regular["bars"]) == 1 and regular["bars"][0]["is_complete"]
    assert [bar["session"] for bar in extended["bars"]] == ["regular", "post"]


def test_indicators_have_numeric_values_and_warmup_nulls(tmp_path):
    source = frame(pd.bdate_range("2026-08-03", "2026-10-23", tz=ET))
    service = MarketDataService(
        tmp_path, provider=Provider(source), clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    )
    result = service.get_indicators(
        "SPY",
        sma_period=3,
        ema_period=3,
        rsi_period=3,
        boll_period=3,
        atr_period=3,
        macd_fast=3,
        macd_slow=5,
        macd_signal=2,
    )
    bars = service.get_bars("SPY", period="3mo")["bars"]
    assert result["points"][0]["sma"] is None
    assert result["points"][1]["sma"] is None
    average = sum(bar["close"] for bar in bars[-3:]) / 3
    last = result["points"][-1]
    assert last["sma"] == average and last["rsi"] == 100
    assert last["atr"] == pytest.approx(2)
    assert last["boll_upper"] == pytest.approx(average + 2 * sqrt(2 / 3))
    assert last["macd"] > 0 and last["macd_signal"] is not None
    with pytest.raises(MarketDataError):
        service.get_indicators("SPY", macd_fast=30, macd_slow=20)


def test_quotes_are_bar_close_and_unavailable_is_null_not_zero(tmp_path):
    source = frame(pd.bdate_range("2026-10-19", "2026-10-23", tz=ET))
    service = MarketDataService(
        tmp_path, provider=Provider(source), clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    )
    available, missing = service.get_quotes(["SPY", "MISSING"])["quotes"]
    assert available["price"] == 15 and available["change"] == 1
    assert available["price_kind"] == "bar_close"
    assert available["meta"]["delay_seconds"] is None
    assert missing["price"] is None and missing["meta"]["quality"] == "unavailable"


def test_http_options_and_window_bounds_are_enforced(tmp_path):
    provider = Provider(frame(pd.bdate_range("2026-10-19", "2026-10-23", tz=ET)))
    service = MarketDataService(
        tmp_path, provider=provider, clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    )
    app = FastAPI()
    app.include_router(create_api_router(market_service_reader=lambda: service))
    with TestClient(app) as client:
        good = client.get("/api/v1/market/bars?symbol=SPY")
        assert good.status_code == 200 and good.json()["meta"]["source"] == "fixture"
        assert client.get("/api/v1/market/capabilities").json()["nbbo"] is False
        assert (
            client.get("/api/v1/market/bars?symbol=SPY&interval=5m&period=3mo").status_code == 422
        )
        assert client.get("/api/v1/market/bars?symbol=SPY&session=extended").status_code == 422
        assert client.get("/api/v1/market/bars?symbol=../private").status_code == 422
        assert client.get("/api/v1/market/bars?symbol=SPY&limit=5001").status_code == 422
        assert (
            client.get(
                "/api/v1/market/quotes?symbols=" + ",".join(f"T{i}" for i in range(51))
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "symbol",
    ["ASML.AS", "SAP.DE", "7203.T", "SHOP.TO", "RIO.AX", "0700.HK", "EURUSD=X", "GC=F", "BTC-USD"],
)
def test_numeric_us_market_endpoints_reject_other_markets_before_fetching(tmp_path, symbol):
    provider = Provider(frame(pd.bdate_range("2026-10-19", "2026-10-23", tz=ET)))
    service = MarketDataService(tmp_path, provider=provider)
    app = FastAPI()
    app.include_router(create_api_router(market_service_reader=lambda: service))
    with TestClient(app) as client:
        for path, params in (
            ("/api/v1/market/bars", {"symbol": symbol}),
            ("/api/v1/market/quotes", {"symbols": symbol}),
            ("/api/v1/market/indicators", {"symbol": symbol}),
        ):
            response = client.get(path, params=params)
            assert response.status_code == 422
            assert response.json()["error_code"] == "market_not_supported"
    assert not provider.calls


@pytest.mark.parametrize("symbol", ["BRK.A", "BRK.B", "BF.A", "BF.B", "BRK-B", "TSM", "BNTX"])
def test_numeric_bars_keep_us_share_classes_and_us_listed_adrs_available(tmp_path, symbol):
    provider = Provider(frame(pd.bdate_range("2026-10-19", "2026-10-23", tz=ET)))
    service = MarketDataService(
        tmp_path, provider=provider, clock=lambda: datetime(2026, 10, 24, 1, tzinfo=UTC)
    )
    result = service.get_bars(symbol.lower())
    assert result["symbol"] == symbol and result["bars"]
    assert provider.calls[0][0] == symbol
