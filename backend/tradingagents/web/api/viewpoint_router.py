"""Owner-scoped manual follow-up; no broker, scheduler or model side effects."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .schemas import ApiError
from .viewpoint_schemas import (
    ResearchComparison,
    ViewpointCreate,
    ViewpointList,
    ViewpointUpdate,
    ViewpointView,
)


def create_viewpoint_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["research-followup"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 503)})

    def call(method, *args, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "研究跟踪服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args, **kwargs)
        except Exception as error:
            if all(hasattr(error, field) for field in ("code", "message", "status_code")):
                return JSONResponse({"error_code": error.code, "message": error.message, "retryable": False},
                    status_code=error.status_code)
            raise

    @router.get("/viewpoints", response_model=ViewpointList, operation_id="listViewpoints")
    def list_viewpoints(ticker: str | None = Query(default=None, max_length=32), run_id: UUID | None = None,
        include_archived: bool = False):
        return call("list", ticker=ticker, run_id=run_id, include_archived=include_archived)

    @router.post("/viewpoints", response_model=ViewpointView, status_code=201, operation_id="createViewpoint")
    def create(body: ViewpointCreate):
        return call("create", body)

    @router.patch("/viewpoints/{viewpoint_id}", response_model=ViewpointView, operation_id="updateViewpoint")
    def update(viewpoint_id: UUID, body: ViewpointUpdate):
        return call("update", str(viewpoint_id), body)

    @router.get("/runs/{run_id}/compare", response_model=ResearchComparison, operation_id="compareResearchRuns")
    def compare(run_id: UUID, other_run_id: UUID):
        return call("compare", str(run_id), str(other_run_id))

    return router
