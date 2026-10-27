"""Theme CRUD and data-only analytics; providers are injected by the runtime."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .schemas import ApiError
from .themes_schemas import (
    ThemeAnalytics,
    ThemeClone,
    ThemeError,
    ThemeList,
    ThemeMemberAdd,
    ThemePin,
    ThemeRevision,
    ThemeUpdate,
    ThemeVersionList,
    ThemeView,
    ThemeWrite,
)


def create_themes_router(service_reader: Callable | None = None, *, prefix="/api/v1"):
    router = APIRouter(
        prefix=prefix + "/themes",
        tags=["themes"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 503)},
    )

    def call(method, *args, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "主题服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args, **kwargs)
        except ThemeError as error:
            return JSONResponse(
                {
                    "error_code": error.code,
                    "message": error.message,
                    "retryable": error.status_code == 503,
                },
                status_code=error.status_code,
            )

    @router.get("", response_model=ThemeList, operation_id="listThemes")
    def themes(include_archived: bool = False):
        return call("list_themes", include_archived=include_archived)

    @router.post("", response_model=ThemeView, operation_id="createTheme", status_code=201)
    def create(body: ThemeWrite):
        return call("create_theme", body)

    @router.get("/{theme_id}", response_model=ThemeView, operation_id="getTheme")
    def get(theme_id: str, version: int | None = Query(default=None, ge=1)):
        return call("get_theme", theme_id, version=version)

    @router.put("/{theme_id}", response_model=ThemeView, operation_id="updateTheme")
    def update(theme_id: str, body: ThemeUpdate):
        return call("update_theme", theme_id, body)

    @router.delete("/{theme_id}", response_model=ThemeView, operation_id="archiveTheme")
    def archive(theme_id: str, body: ThemeRevision):
        return call("archive_theme", theme_id, body.revision)

    @router.patch("/{theme_id}/pin", response_model=ThemeView, operation_id="setThemePinned")
    def pin(theme_id: str, body: ThemePin):
        return call("set_theme_pinned", theme_id, body)

    @router.post("/{theme_id}/restore", response_model=ThemeView, operation_id="restoreTheme")
    def restore(theme_id: str, body: ThemeRevision):
        return call("restore_theme", theme_id, body.revision)

    @router.post(
        "/{theme_id}/clone", response_model=ThemeView, operation_id="cloneTheme", status_code=201
    )
    def clone(theme_id: str, body: ThemeClone):
        return call("clone_theme", theme_id, body)

    @router.get(
        "/{theme_id}/versions", response_model=ThemeVersionList, operation_id="listThemeVersions"
    )
    def versions(theme_id: str):
        return call("list_versions", theme_id)

    @router.post("/{theme_id}/members", response_model=ThemeView, operation_id="addThemeMember")
    def add(theme_id: str, body: ThemeMemberAdd):
        return call("add_member", theme_id, body)

    @router.delete(
        "/{theme_id}/members/{symbol}", response_model=ThemeView, operation_id="removeThemeMember"
    )
    def remove(theme_id: str, symbol: str, revision: int = Query(ge=1)):
        return call("remove_member", theme_id, symbol, revision)

    @router.get(
        "/{theme_id}/analytics", response_model=ThemeAnalytics, operation_id="getThemeAnalytics"
    )
    def analytics(
        theme_id: str,
        window: Literal["1D", "5D", "1M"] = "1D",
        refresh: bool = False,
        version: int | None = Query(default=None, ge=1),
        documented_only: bool = False,
    ):
        return call(
            "get_analytics",
            theme_id,
            window=window,
            refresh=refresh,
            version=version,
            documented_only=documented_only,
        )

    return router
