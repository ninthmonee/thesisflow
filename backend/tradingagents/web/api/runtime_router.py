"""Runtime HTTP boundary; service construction and process probing are injected."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .model_schemas import RevisionRequest
from .runtime_schemas import (
    RuntimeConfigurationError,
    RuntimeConnectionCreate,
    RuntimeConnectionList,
    RuntimeConnectionUpdate,
    RuntimeConnectionView,
    RuntimeTestResult,
    RuntimeTypeList,
)
from .schemas import ApiError


class RuntimeRoute(APIRoute):
    def get_route_handler(self):
        operation = super().get_route_handler()

        async def redacted(request):
            try:
                return await operation(request)
            except RequestValidationError:
                return JSONResponse({"error_code": "invalid_runtime_request",
                    "message": "运行环境输入无效，请检查程序路径、模型ID和配置版本。", "retryable": False}, status_code=422)

        return redacted


def create_runtime_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["agent-runtimes"], route_class=RuntimeRoute, responses={
        code: {"model": ApiError} for code in (400, 403, 404, 409, 422, 503)
    })

    def call(operation, *args):
        if service_reader is None:
            raise HTTPException(503, "运行环境服务尚未初始化")
        try:
            return getattr(service_reader(), operation)(*args)
        except RuntimeConfigurationError as error:
            return JSONResponse({"error_code": error.code, "message": error.message,
                "retryable": error.status_code in {429, 503}}, status_code=error.status_code)

    @router.get("/runtime-types", response_model=RuntimeTypeList, operation_id="listRuntimeTypes")
    def types():
        result = call("list_types")
        return result if isinstance(result, Response) else {"types": result}

    @router.get("/runtime-connections", response_model=RuntimeConnectionList,
        operation_id="listRuntimeConnections")
    def connections():
        result = call("list_connections")
        return result if isinstance(result, Response) else {"connections": result}

    @router.post("/runtime-connections", response_model=RuntimeConnectionView, status_code=201,
        operation_id="createRuntimeConnection")
    def create(body: RuntimeConnectionCreate):
        return call("create_connection", body.model_dump())

    @router.patch("/runtime-connections/{connection_id}", response_model=RuntimeConnectionView,
        operation_id="updateRuntimeConnection")
    def update(connection_id: UUID, body: RuntimeConnectionUpdate):
        return call("update_connection", str(connection_id), body.model_dump(exclude_unset=True))

    @router.delete("/runtime-connections/{connection_id}", status_code=204,
        operation_id="deleteRuntimeConnection")
    def delete(connection_id: UUID, revision: int = Query(ge=1)):
        result = call("delete_connection", str(connection_id), revision)
        return result if isinstance(result, Response) else Response(status_code=204)

    @router.post("/runtime-connections/{connection_id}/test", response_model=RuntimeTestResult,
        operation_id="testRuntimeConnection")
    def test(connection_id: UUID, body: RevisionRequest):
        return call("test_connection", str(connection_id), body.revision)

    return router
