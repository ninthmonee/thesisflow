"""Owner-scoped read-only research evidence endpoints, without runtime imports."""

from collections.abc import Mapping
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .research_evidence_schemas import ResearchEvidencePage, ResearchQualitySummary
from .schemas import ApiError


def create_research_evidence_router(service_reader=None, scope_reader=None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix,
        tags=["research-evidence"],
        responses={status: {"model": ApiError} for status in (403, 404, 422, 503)},
    )

    def call(method, run_id, **parameters):
        if service_reader is None or scope_reader is None:
            raise HTTPException(503, "研究证据服务尚未初始化")
        from tradingagents.web.domain.tasks import ResearchError

        scope = scope_reader()
        if isinstance(scope, Mapping):
            workspace_id = scope.get("workspace_id")
            owner_id = scope.get("owner_id", scope.get("user_id"))
        else:
            workspace_id, owner_id = scope.workspace_id, scope.user_id
        try:
            return getattr(service_reader(), method)(
                str(run_id), workspace_id=workspace_id, owner_id=owner_id, **parameters
            )
        except ResearchError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    @router.get(
        "/runs/{run_id}/evidence",
        response_model=ResearchEvidencePage,
        operation_id="getResearchEvidence",
    )
    def evidence(
        run_id: UUID,
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=64),
        include_body: bool = False,
    ):
        return call("list_evidence", run_id, limit=limit, cursor=cursor, include_body=include_body)

    @router.get(
        "/runs/{run_id}/quality",
        response_model=ResearchQualitySummary,
        operation_id="getResearchQuality",
    )
    def quality(run_id: UUID):
        return call("get_quality", run_id)

    return router
