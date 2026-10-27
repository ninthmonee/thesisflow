"""Independent Yahoo adapters for the Web market views and instrument catalog.

These return vendor-native rows for the application's calendar-aware numeric
normalization. They do not use the research tools' historical daily transforms.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

import pandas as pd

from tradingagents.dataflows.errors import VendorUnavailableError


class YahooHistoryProvider:
    source = "yahoo"
    feed = "yahoo_chart"

    def __init__(self, cache_root: Path):
        self.cache_root = cache_root
        self._lock = RLock()

    def history(self, symbol: str, **kwargs) -> pd.DataFrame:
        import yfinance as yf

        try:
            with self._lock:
                owned = self.cache_root / "yahoo-runtime"
                owned.mkdir(parents=True, exist_ok=True, mode=0o700)
                yf.set_tz_cache_location(str(owned))
            vendor_symbol = (
                symbol.replace(".", "-")
                if symbol in {"BRK.B", "BRK.A", "BF.B", "BF.A"}
                else symbol
            )
            return yf.Ticker(vendor_symbol).history(**kwargs)
        except Exception:
            raise VendorUnavailableError("Yahoo market history request failed") from None


class YahooSearchProvider:
    def __call__(self, query: str) -> list[dict[str, Any]]:
        import yfinance as yf

        try:
            result = yf.Search(
                query, max_results=15, news_count=0, lists_count=0, timeout=10,
                raise_errors=True,
            )
            supported = {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "BTS", "NASDAQ", "NYSE"}
            instruments = []
            for item in result.quotes:
                if item.get("quoteType") not in {"EQUITY", "ETF"} or item.get("exchange") not in supported:
                    continue
                symbol = item.get("symbol")
                if not isinstance(symbol, str) or not symbol:
                    continue
                instruments.append(
                    {
                        "symbol": symbol,
                        "name": item.get("longname") or item.get("shortname") or symbol,
                        "exchange": item.get("exchange", ""),
                        "asset_type": "etf" if item.get("quoteType") == "ETF" else "stock",
                        "currency": "USD",
                        "timezone": "America/New_York",
                    }
                )
            return instruments
        except Exception:
            raise VendorUnavailableError("Yahoo instrument search request failed") from None
