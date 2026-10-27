"""Explicit data configuration and application-owned rebuildable cache operations."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .data_schemas import (
    DataCacheClearResponse,
    DataSettingsError,
    DataSettingsResponse,
    DataSettingsServiceProtocol,
    DataSettingsUpdate,
    DataStatusResponse,
)
from .schemas import ApiError


def create_data_router(
    service_reader: Callable[[], DataSettingsServiceProtocol] | None = None,
    *,
    prefix: str = "/api/v1",
) -> APIRouter:
    router = APIRouter(
        prefix=prefix,
        tags=["data-center"],
        responses={status: {"model": ApiError} for status in (403, 409, 422, 503)},
    )

    def call(method, *args):
        if service_reader is None:
            raise HTTPException(503, "数据中心服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args)
        except DataSettingsError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    @router.get(
        "/data-settings", response_model=DataSettingsResponse, operation_id="getDataSettings"
    )
    def settings():
        return call("get_settings")

    @router.put(
        "/data-settings", response_model=DataSettingsResponse, operation_id="updateDataSettings"
    )
    def update(body: DataSettingsUpdate):
        return call("update_settings", body)

    @router.post(
        "/data-settings/apply",
        response_model=DataSettingsResponse,
        operation_id="applyDataSettings",
    )
    def apply():
        return call("apply_saved_settings")

    @router.get("/data-status", response_model=DataStatusResponse, operation_id="getDataStatus")
    def status():
        return call("get_status")

    @router.post(
        "/data-cache/clear", response_model=DataCacheClearResponse, operation_id="clearMarketCache"
    )
    def clear():
        return call("clear_market_cache")

    return router
