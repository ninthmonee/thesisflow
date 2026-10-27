"""Manage scoped research accounts separately from public market adapters."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .research_data_schemas import (
    ResearchDataConnectionCreate,
    ResearchDataConnectionError,
    ResearchDataConnectionList,
    ResearchDataConnectionRevision,
    ResearchDataConnectionTestResponse,
    ResearchDataConnectionUpdate,
    ResearchDataConnectionView,
)
from .schemas import ApiError


def create_research_data_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix + "/research-data-connections",
        tags=["research-data-connections"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 429, 503)},
    )

    def call(method, *args):
        if service_reader is None:
            raise HTTPException(503, "研究数据连接服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args)
        except ResearchDataConnectionError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code in {429, 503},
                },
                status_code=error.status_code,
            )

    @router.get(
        "", response_model=ResearchDataConnectionList, operation_id="listResearchDataConnections"
    )
    def connections():
        result = call("list_connections")
        return result if isinstance(result, JSONResponse) else {"connections": result}

    @router.post(
        "",
        response_model=ResearchDataConnectionView,
        status_code=201,
        operation_id="createResearchDataConnection",
    )
    def create(body: ResearchDataConnectionCreate):
        return call("create_connection", body)

    @router.patch(
        "/{connection_id}",
        response_model=ResearchDataConnectionView,
        operation_id="updateResearchDataConnection",
    )
    def update(connection_id: str, body: ResearchDataConnectionUpdate):
        return call("update_connection", connection_id, body)

    @router.post(
        "/{connection_id}/clear",
        response_model=ResearchDataConnectionView,
        operation_id="clearResearchDataConnection",
    )
    def clear(connection_id: str, body: ResearchDataConnectionRevision):
        return call("clear_connection", connection_id, body.revision)

    @router.post(
        "/{connection_id}/test",
        response_model=ResearchDataConnectionTestResponse,
        operation_id="testResearchDataConnection",
    )
    def test(connection_id: str, body: ResearchDataConnectionRevision):
        return call("test_connection", connection_id, body.revision)

    return router
