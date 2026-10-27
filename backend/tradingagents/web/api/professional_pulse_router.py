"""Explicitly requested professional dimensions; no implicit symbols or account calls."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .intelligence_schemas import IntelligenceError, ProfessionalMarketPulse
from .schemas import ApiError


def create_professional_pulse_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix,
        tags=["professional-market-pulse"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 503)},
    )

    @router.get(
        "/market/professional-pulse",
        response_model=ProfessionalMarketPulse,
        operation_id="getProfessionalMarketPulse",
    )
    def professional_pulse(
        options_symbols: str | None = Query(default=None, max_length=200),
        etf_symbols: str | None = Query(default=None, max_length=400),
        news_symbol: str | None = Query(default=None, max_length=32),
        include_news: bool = False,
        options_provider: Literal["yahoo", "massive", "alpaca"] = "yahoo",
        flows_provider: Literal["ishares", "intrinio"] = "ishares",
        news_provider: Literal["massive"] = "massive",
        options_connection_id: str | None = Query(default=None, max_length=36),
        flows_connection_id: str | None = Query(default=None, max_length=36),
        news_connection_id: str | None = Query(default=None, max_length=36),
        lookback_days: int = Query(default=30, ge=1, le=90),
        lookback_hours: int = Query(default=24, ge=1, le=168),
        refresh: bool = False,
    ):
        if service_reader is None:
            raise HTTPException(503, "专业市场维度服务尚未初始化")
        try:
            return service_reader().get_pulse(
                options_symbols=options_symbols.split(",") if options_symbols else [],
                etf_symbols=etf_symbols.split(",") if etf_symbols else [],
                news_symbol=news_symbol,
                include_news=include_news,
                options_provider=options_provider,
                flows_provider=flows_provider,
                news_provider=news_provider,
                options_connection_id=options_connection_id,
                flows_connection_id=flows_connection_id,
                news_connection_id=news_connection_id,
                lookback_days=lookback_days,
                lookback_hours=lookback_hours,
                refresh=refresh,
            )
        except IntelligenceError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    return router
