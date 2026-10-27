"""Pure watchlist HTTP contracts; runtime use cases are supplied by the caller."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstrumentResponse(BaseModel):
    id: str
    symbol: str
    name: str
    asset_type: str
    exchange: str
    currency: str
    timezone: str


class WatchlistItemResponse(BaseModel):
    id: str
    position: int
    instrument: InstrumentResponse
    favorite: bool = False
    notes: str = ""
    observation_revision: int = 0


class WatchlistObservationPatch(Input):
    revision: int = Field(ge=0, strict=True)
    favorite: bool = Field(default=False, strict=True)
    notes: str = Field(default="", max_length=2000, strict=True)

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set.intersection({"favorite", "notes"}):
            raise ValueError("请至少提交收藏状态或观察备注")
        return self


class WatchlistObservationResponse(BaseModel):
    instrument_id: str
    favorite: bool
    notes: str
    revision: int
    updated_at: datetime | None = None


class WatchlistResponse(BaseModel):
    id: str
    name: str
    position: int
    revision: int
    items: list[WatchlistItemResponse]


class SearchResponse(BaseModel):
    items: list[InstrumentResponse]
    source: str
    quality: str
    warning: str | None = None


class NewWatchlist(Input):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("分组名称不能为空")
        return value.strip()


class RenameWatchlist(NewWatchlist):
    revision: int = Field(ge=1)


class AddWatchlistItem(Input):
    instrument_id: str
    revision: int = Field(ge=1)


class ReorderWatchlistItems(Input):
    item_ids: list[str]
    revision: int = Field(ge=1)


def create_watchlist_router(
    service_reader: Callable[[], Any] | None = None, *, prefix: str = "/api/v1"
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["watchlists"])

    def service():
        if service_reader is None:
            raise HTTPException(503, "自选服务尚未初始化")
        return service_reader()

    def call(name: str, *args, **kwargs):
        try:
            return getattr(service(), name)(*args, **kwargs)
        except Exception as exc:
            if all(hasattr(exc, key) for key in ("status_code", "code", "message")):
                raise HTTPException(
                    exc.status_code, {"error_code": exc.code, "message": exc.message}
                ) from None
            raise

    @router.get(
        "/instruments/search", response_model=SearchResponse, operation_id="searchInstruments"
    )
    def search(q: str = Query(default="", max_length=80)):
        return call("search_instruments", q)

    @router.get(
        "/watchlists", response_model=list[WatchlistResponse], operation_id="listWatchlists"
    )
    def list_groups():
        return call("list_watchlists")

    @router.patch(
        "/watchlist-observations/{instrument_id}",
        response_model=WatchlistObservationResponse,
        operation_id="updateWatchlistObservation",
    )
    def observation(instrument_id: str, body: WatchlistObservationPatch):
        return call("update_observation", instrument_id, body)

    @router.post(
        "/watchlists",
        response_model=WatchlistResponse,
        status_code=201,
        operation_id="createWatchlist",
    )
    def create(body: NewWatchlist):
        return call("create_watchlist", body.name)

    @router.patch(
        "/watchlists/{list_id}", response_model=WatchlistResponse, operation_id="renameWatchlist"
    )
    def rename(list_id: str, body: RenameWatchlist):
        return call("rename_watchlist", list_id, body.name, body.revision)

    @router.delete("/watchlists/{list_id}", status_code=204, operation_id="deleteWatchlist")
    def remove_group(list_id: str, revision: int = Query(ge=1)):
        call("delete_watchlist", list_id, revision)
        return Response(status_code=204)

    @router.post(
        "/watchlists/{list_id}/items",
        response_model=WatchlistResponse,
        operation_id="addWatchlistItem",
    )
    def add(list_id: str, body: AddWatchlistItem):
        return call("add_item", list_id, body.instrument_id, body.revision)

    @router.delete(
        "/watchlists/{list_id}/items/{item_id}",
        response_model=WatchlistResponse,
        operation_id="removeWatchlistItem",
    )
    def remove(list_id: str, item_id: str, revision: int = Query(ge=1)):
        return call("remove_item", list_id, item_id, revision)

    @router.put(
        "/watchlists/{list_id}/items/order",
        response_model=WatchlistResponse,
        operation_id="reorderWatchlistItems",
    )
    def reorder(list_id: str, body: ReorderWatchlistItems):
        return call("reorder_items", list_id, body.item_ids, body.revision)

    return router
