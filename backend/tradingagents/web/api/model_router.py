"""Pure model routes: runtime services are injected, never imported for export."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from .model_schemas import (
    ConnectionCreate,
    ConnectionList,
    ConnectionUpdate,
    ConnectionView,
    ModelConfigurationError,
    ModelDiscovery,
    ModelServiceProtocol,
    ModelSettings,
    ModelTestResult,
    ProfileCreate,
    ProfileList,
    ProfileUpdate,
    ProfileView,
    ProviderList,
    RevisionRequest,
    TemplateList,
    TemplateView,
    TemplateWrite,
)
from .schemas import ApiError


def create_model_router(
    service_reader: Callable[[], ModelServiceProtocol] | None = None,
    *, prefix: str = "/api/v1",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["models"], responses={
        status: {"model": ApiError} for status in (400, 404, 409, 503)
    })

    def call(method: str, *args):
        if service_reader is None:
            raise HTTPException(503, detail="模型配置服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args)
        except ModelConfigurationError as exc:
            return JSONResponse({"error_code": exc.code, "message": exc.message,
                                 "retryable": exc.status in {429, 503}}, status_code=exc.status)

    @router.get("/model-providers", response_model=ProviderList, operation_id="listModelProviders")
    def providers():
        result = call("providers")
        return result if isinstance(result, Response) else {"providers": result}

    @router.get("/model-connections", response_model=ConnectionList, operation_id="listModelConnections")
    def connections():
        result = call("list_connections")
        return result if isinstance(result, Response) else {"connections": result}

    @router.post("/model-connections", response_model=ConnectionView, status_code=201,
                 operation_id="createModelConnection")
    def create_connection(body: ConnectionCreate):
        return call("create_connection", body.model_dump())

    @router.patch("/model-connections/{connection_id}", response_model=ConnectionView,
                  operation_id="updateModelConnection")
    def update_connection(connection_id: str, body: ConnectionUpdate):
        return call("update_connection", connection_id, body.model_dump(exclude_unset=True))

    @router.delete("/model-connections/{connection_id}", status_code=204,
                   operation_id="deleteModelConnection")
    def delete_connection(connection_id: str, revision: int = Query(ge=1)):
        result = call("delete_connection", connection_id, revision)
        return result if isinstance(result, Response) else Response(status_code=204)

    @router.get("/model-connections/{connection_id}/models", response_model=ModelDiscovery,
                operation_id="discoverConnectionModels")
    def discover_models(connection_id: str):
        return call("discover_models", connection_id)

    @router.get("/model-profiles", response_model=ProfileList, operation_id="listModelProfiles")
    def profiles():
        result = call("list_profiles")
        return result if isinstance(result, Response) else {"profiles": result}

    @router.post("/model-profiles", response_model=ProfileView, status_code=201,
                 operation_id="createModelProfile")
    def create_profile(body: ProfileCreate):
        return call("create_profile", body.model_dump())

    @router.patch("/model-profiles/{profile_id}", response_model=ProfileView,
                  operation_id="updateModelProfile")
    def update_profile(profile_id: str, body: ProfileUpdate):
        return call("update_profile", profile_id, body.model_dump(exclude_unset=True))

    @router.post("/model-profiles/{profile_id}/test", response_model=ModelTestResult,
                 operation_id="testModelProfile")
    def test_profile(profile_id: str, body: RevisionRequest):
        return call("test_profile", profile_id, body.revision)

    @router.get("/model-settings", response_model=ModelSettings, operation_id="getModelSettings")
    def settings():
        return call("settings")

    @router.put("/model-settings", response_model=TemplateView, operation_id="saveModelSettings")
    def save_settings(body: TemplateWrite):
        return call("save_settings", body.model_dump())

    @router.get("/research-templates", response_model=TemplateList, operation_id="listResearchTemplates")
    def templates():
        result = call("list_templates")
        return result if isinstance(result, Response) else {"templates": result}

    return router
