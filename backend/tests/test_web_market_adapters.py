"""Web adapters preserve native rows and sanitize vendor failures at the data boundary."""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.web_market import YahooHistoryProvider, YahooSearchProvider


def test_history_passes_native_minute_rows_and_owned_cache_through(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        {"Close": [123.0]}, index=pd.DatetimeIndex(["2026-10-23T13:35:00Z"])
    )
    calls = {}

    def ticker(symbol):
        calls["symbol"] = symbol

        def history(**kwargs):
            calls["kwargs"] = kwargs
            return frame

        return SimpleNamespace(history=history)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=ticker, set_tz_cache_location=lambda path: calls.update(cache=path)
    ))
    options = {"interval": "5m", "period": "1mo", "prepost": True,
               "auto_adjust": False, "timeout": 15}
    result = YahooHistoryProvider(tmp_path).history("BRK.B", **options)
    assert result is frame
    assert calls == {"symbol": "BRK-B", "kwargs": options,
                     "cache": str(tmp_path / "yahoo-runtime")}


def test_search_filters_to_supported_us_stocks_and_etfs(monkeypatch):
    rows = [
        {"symbol": "NVDA", "exchange": "NMS", "quoteType": "EQUITY", "longname": "NVIDIA"},
        {"symbol": "SPY", "exchange": "PCX", "quoteType": "ETF", "shortname": "SPDR"},
        {"symbol": "000001.SZ", "exchange": "SHZ", "quoteType": "EQUITY"},
        {"symbol": "^VIX", "exchange": "WCB", "quoteType": "INDEX"},
    ]
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Search=lambda *_args, **_kwargs: SimpleNamespace(quotes=rows)
    ))
    result = YahooSearchProvider()("example")
    assert [(item["symbol"], item["asset_type"]) for item in result] == [
        ("NVDA", "stock"), ("SPY", "etf")
    ]


@pytest.mark.parametrize("operation", ["history", "search"])
def test_vendor_errors_never_forward_private_response(operation, tmp_path, monkeypatch):
    def failure(*_args, **_kwargs):
        raise RuntimeError("https://private.invalid?api_key=DO-NOT-EXPOSE")

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=failure, Search=failure, set_tz_cache_location=lambda _path: None
    ))
    with pytest.raises(VendorUnavailableError) as caught:
        if operation == "history":
            YahooHistoryProvider(tmp_path).history("SPY", interval="5m", period="1mo")
        else:
            YahooSearchProvider()("NVDA")
    assert "private.invalid" not in str(caught.value)
    assert "DO-NOT-EXPOSE" not in str(caught.value)
    assert caught.value.__suppress_context__
