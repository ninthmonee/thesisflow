"""Read-only runtime readiness; execution services are injected only at runtime."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .readiness_schemas import RuntimeReadiness


def create_readiness_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["runtime-readiness"])

    @router.get("/readiness", response_model=RuntimeReadiness, operation_id="getRuntimeReadiness")
    def readiness():
        if service_reader is None:
            raise HTTPException(503, "运行状态服务尚未初始化")
        try:
            return service_reader().get_readiness()
        except Exception as error:
            if all(hasattr(error, key) for key in ("code", "message", "status_code")):
                return JSONResponse(
                    {"error_code": error.code, "message": error.message, "retryable": False},
                    status_code=error.status_code,
                )
            raise

    return router
