"""Read public market snapshots; model interpretation requires explicit POST."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .intelligence_schemas import (
    IntelligenceError,
    IntelligenceServiceProtocol,
    MarketBrief,
    MarketBriefRequest,
    MarketSnapshot,
    MarketWindow,
)
from .schemas import ApiError


def create_intelligence_router(
    service_reader: Callable[[], IntelligenceServiceProtocol] | None = None,
    brief_generator_reader: Callable | None = None,
    *,
    prefix: str = "/api/v1",
) -> APIRouter:
    router = APIRouter(
        prefix=prefix,
        tags=["market-intelligence"],
        responses={status: {"model": ApiError} for status in (404, 409, 422, 503)},
    )

    def service():
        if service_reader is None:
            raise HTTPException(503, "市场聚合服务尚未初始化")
        return service_reader()

    @router.get("/market/pulse", response_model=MarketSnapshot, operation_id="getMarketPulse")
    def pulse(window: MarketWindow = "1D", refresh: bool = False):
        try:
            return service().get_snapshot(window, refresh=refresh)
        except IntelligenceError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    @router.post("/market/briefs", response_model=MarketBrief, operation_id="generateMarketBrief")
    def brief(body: MarketBriefRequest):
        try:
            generator = brief_generator_reader() if brief_generator_reader is not None else None
            return service().generate_brief(body.snapshot_id, body.language, generator=generator)
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
