"""Read-only watchlist tracking and an immutable, inspectable rule set."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .schemas import ApiError
from .tracking_schemas import TrackingError, TrackingRules, WatchlistTrackingResponse


def create_tracking_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix,
        tags=["watchlist-tracking"],
        responses={status: {"model": ApiError} for status in (403, 404, 422, 503)},
    )

    def call(method, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "自选技术观察服务尚未初始化")
        try:
            return getattr(service_reader(), method)(**kwargs)
        except TrackingError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    @router.get(
        "/watchlist-tracking",
        response_model=WatchlistTrackingResponse,
        operation_id="getWatchlistTracking",
    )
    def tracking(
        watchlist_id: str | None = Query(default=None, min_length=1, max_length=64),
        instrument_ids: str | None = Query(default=None, min_length=1, max_length=6500),
        refresh: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return call(
            "get_tracking",
            watchlist_id=watchlist_id,
            instrument_ids=instrument_ids.split(",") if instrument_ids else None,
            refresh=refresh,
            limit=limit,
        )

    @router.get(
        "/watchlist-tracking/rules",
        response_model=TrackingRules,
        operation_id="getWatchlistTrackingRules",
    )
    def rules():
        return call("rules")

    return router
