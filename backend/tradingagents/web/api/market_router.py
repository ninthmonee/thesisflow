"""Thin market HTTP API; provider calls are supplied by the runtime service."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .market_schemas import (
    BarsResponse,
    CalendarResponse,
    IndicatorsResponse,
    MarketAdjustment,
    MarketCapabilities,
    MarketInterval,
    MarketPeriod,
    MarketSession,
    QuotesResponse,
)
from .schemas import ApiError


def create_market_router(service_reader: Callable[[], Any] | None = None, *,
                         prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/market", tags=["market"], responses={
        code: {"model": ApiError} for code in (400, 422, 503)
    })

    def call(method, *args, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "行情服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args, **kwargs)
        except Exception as exc:
            if all(hasattr(exc, key) for key in ("status_code", "code", "message")):
                return JSONResponse({"error_code": exc.code, "message": exc.message,
                                     "retryable": exc.status_code == 503}, status_code=exc.status_code)
            raise

    @router.get("/capabilities", response_model=MarketCapabilities, operation_id="getMarketCapabilities")
    def capabilities():
        return call("capabilities")

    @router.get("/bars", response_model=BarsResponse, operation_id="getMarketBars")
    def bars(symbol: str = Query(min_length=1, max_length=32), interval: MarketInterval = "1d",
             period: MarketPeriod = "1mo", session: MarketSession = "regular",
             adjustment: MarketAdjustment = "provider_native", limit: int = Query(default=5000, ge=1, le=5000)):
        return call("get_bars", symbol, interval=interval, period=period, session=session,
                    adjustment=adjustment, limit=limit)

    @router.get("/quotes", response_model=QuotesResponse, operation_id="getMarketQuotes")
    def quotes(symbols: str = Query(min_length=1, max_length=1700)):
        return call("get_quotes", symbols.split(","))

    @router.get("/indicators", response_model=IndicatorsResponse, operation_id="getMarketIndicators")
    def indicators(symbol: str = Query(min_length=1, max_length=32), interval: MarketInterval = "1d",
                   period: MarketPeriod = "3mo", session: MarketSession = "regular",
                   adjustment: MarketAdjustment = "provider_native",
                   sma_period: int = Query(default=20, ge=2, le=200),
                   ema_period: int = Query(default=20, ge=2, le=200),
                   rsi_period: int = Query(default=14, ge=2, le=200),
                   macd_fast: int = Query(default=12, ge=2, le=100),
                   macd_slow: int = Query(default=26, ge=3, le=200),
                   macd_signal: int = Query(default=9, ge=2, le=100),
                   boll_period: int = Query(default=20, ge=2, le=200),
                   boll_deviation: float = Query(default=2, ge=0.1, le=5),
                   atr_period: int = Query(default=14, ge=2, le=200)):
        return call("get_indicators", symbol, interval=interval, period=period, session=session,
                    adjustment=adjustment, sma_period=sma_period, ema_period=ema_period,
                    rsi_period=rsi_period, macd_fast=macd_fast, macd_slow=macd_slow,
                    macd_signal=macd_signal, boll_period=boll_period,
                    boll_deviation=boll_deviation, atr_period=atr_period)

    @router.get("/calendar", response_model=CalendarResponse, operation_id="getMarketCalendar")
    def calendar(start: str = Query(min_length=10, max_length=10),
                 end: str = Query(min_length=10, max_length=10)):
        return call("get_calendar", start, end)

    return router
