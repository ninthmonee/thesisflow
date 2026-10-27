"""Supplemental input snapshots are prepared explicitly without calling a model."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .research_context_schemas import ResearchContextCreate, ResearchContextView
from .schemas import ApiError


def create_research_context_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["research-context"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 503)})

    def call(method, *args):
        if service_reader is None:
            raise HTTPException(503, "研究资料服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args)
        except Exception as error:
            if all(hasattr(error, attr) for attr in ("code", "message", "status_code")):
                return JSONResponse({"error_code": error.code, "message": error.message,
                    "retryable": False}, status_code=error.status_code)
            raise

    @router.post("/research-context", response_model=ResearchContextView, status_code=201,
        operation_id="createResearchContext")
    def create(body: ResearchContextCreate):
        return call("create", body)

    @router.get("/research-context/{context_id}", response_model=ResearchContextView,
        operation_id="getResearchContext")
    def get(context_id: UUID):
        return call("get", str(context_id))

    return router
