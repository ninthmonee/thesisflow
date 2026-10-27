"""Read-only alternative market endpoints and explicit local connection management."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .alternative_schemas import (
    AlternativeCapabilities,
    AlternativeDataError,
    DataConnectionCreate,
    DataConnectionList,
    DataConnectionTestRequest,
    DataConnectionTestResponse,
    DataConnectionUpdate,
    DataConnectionView,
    ETFFlowsResponse,
    NewsSentimentResponse,
    OptionsResponse,
)
from .schemas import ApiError


def create_alternative_router(service_reader: Callable | None = None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix,
        tags=["alternative-market-data"],
        responses={status: {"model": ApiError} for status in (400, 403, 404, 409, 422, 429, 503)},
    )

    def call(method, *args, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "辅助市场数据服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args, **kwargs)
        except AlternativeDataError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code in {429, 503},
                },
                status_code=error.status_code,
            )

    @router.get(
        "/data-connections", response_model=DataConnectionList, operation_id="listDataConnections"
    )
    def connections():
        result = call("list_connections")
        return result if isinstance(result, JSONResponse) else {"connections": result}

    @router.post(
        "/data-connections",
        response_model=DataConnectionView,
        operation_id="createDataConnection",
        status_code=201,
    )
    def create(body: DataConnectionCreate):
        return call("create_connection", body)

    @router.patch(
        "/data-connections/{connection_id}",
        response_model=DataConnectionView,
        operation_id="updateDataConnection",
    )
    def update(connection_id: str, body: DataConnectionUpdate):
        return call("update_connection", connection_id, body)

    @router.post(
        "/data-connections/{connection_id}/test",
        response_model=DataConnectionTestResponse,
        operation_id="testDataConnection",
    )
    def test(connection_id: str, body: DataConnectionTestRequest):
        return call("test_connection", connection_id, body)

    @router.get(
        "/market/alternative-capabilities",
        response_model=AlternativeCapabilities,
        operation_id="getAlternativeCapabilities",
    )
    def capabilities():
        return call("capabilities")

    @router.get("/market/etf-flows", response_model=ETFFlowsResponse, operation_id="getETFFlows")
    def flows(
        symbols: str = Query(default="SOXX,IVV", min_length=1, max_length=340),
        provider: Literal["ishares", "intrinio"] = "ishares",
        connection_id: str | None = None,
        lookback_days: int = Query(default=30, ge=1, le=365),
    ):
        return call(
            "get_etf_flows",
            symbols.split(","),
            provider=provider,
            connection_id=connection_id,
            lookback_days=lookback_days,
        )

    @router.get("/market/options", response_model=OptionsResponse, operation_id="getMarketOptions")
    def options(
        symbol: str = Query(min_length=1, max_length=32),
        provider: Literal["yahoo", "massive", "alpaca"] = "yahoo",
        connection_id: str | None = None,
        expiration: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        max_expirations: int = Query(default=1, ge=1, le=3),
    ):
        return call(
            "get_options",
            symbol,
            provider=provider,
            connection_id=connection_id,
            expiration=expiration,
            max_expirations=max_expirations,
        )

    @router.get(
        "/market/news-sentiment",
        response_model=NewsSentimentResponse,
        operation_id="getMarketNewsSentiment",
    )
    def news(
        provider: Literal["massive"] = "massive",
        connection_id: str | None = None,
        symbol: str | None = Query(default=None, min_length=1, max_length=32),
        lookback_hours: int = Query(default=24, ge=1, le=168),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        return call(
            "get_news_sentiment",
            provider=provider,
            connection_id=connection_id,
            symbol=symbol,
            lookback_hours=lookback_hours,
            limit=limit,
        )

    return router
