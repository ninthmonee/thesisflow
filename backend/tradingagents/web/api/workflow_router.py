"""Read-only topology endpoint; the pure definition reader is injected at runtime."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from .workflow_schemas import ResearchWorkflowDefinition


def create_workflow_router(workflow_reader: Callable | None = None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["research-workflow"])

    @router.get(
        "/research-workflow",
        response_model=ResearchWorkflowDefinition,
        operation_id="getResearchWorkflow",
    )
    def workflow():
        if workflow_reader is None:
            raise HTTPException(503, "研究流程定义尚未初始化")
        return workflow_reader()

    return router
