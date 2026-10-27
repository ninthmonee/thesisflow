"""Scoped durable-research HTTP and SSE contracts without runtime imports."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .schemas import (
    ApiError,
    ContractModel,
    CreateResearchRequest,
    EventEnvelope,
    RunSnapshot,
    RunStatus,
)


class RunPage(ContractModel):
    items: list[RunSnapshot]
    next_cursor: str | None = None


class RunEvents(ContractModel):
    events: list[EventEnvelope]


class ManifestResponse(ContractModel):
    schema_version: str
    graph_contract_version: str
    workspace_id: str
    owner_id: str
    request: CreateResearchRequest
    asset_type: str
    runtime: dict[str, Any]
    selection: dict[str, Any]
    data_connections: dict[str, Any] | None = None
    memory_scope: str


def create_research_router(
    service_reader: Callable[[], Any] | None = None,
    scope_reader: Callable[[], Any] | None = None,
    selection_resolver: Callable[[CreateResearchRequest], Mapping[str, Any]] | None = None,
    *,
    prefix: str = "/api/v1",
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 15,
) -> APIRouter:
    router = APIRouter(
        prefix=prefix,
        tags=["research"],
        responses={status: {"model": ApiError} for status in (400, 403, 404, 409, 422, 503)},
    )
    terminal = {"completed", "cancelled", "failed", "interrupted"}

    def context():
        if service_reader is None or scope_reader is None:
            raise HTTPException(503, "研究服务尚未初始化")
        scope = scope_reader()
        if isinstance(scope, Mapping):
            workspace_id, owner_id = (
                scope.get("workspace_id"),
                scope.get("owner_id", scope.get("user_id")),
            )
        else:
            workspace_id, owner_id = scope.workspace_id, scope.user_id
        if not workspace_id or not owner_id:
            raise HTTPException(503, "工作区尚未初始化")
        return service_reader(), {"workspace_id": workspace_id, "owner_id": owner_id}

    def guarded(operation):
        try:
            return operation()
        except Exception as exc:
            if all(hasattr(exc, key) for key in ("code", "message")):
                status = getattr(exc, "status_code", getattr(exc, "status", 400))
                return JSONResponse(
                    {
                        "error_code": exc.code,
                        "message": exc.message,
                        "retryable": status in {429, 503},
                    },
                    status_code=status,
                )
            raise

    def selection(body):
        return dict(selection_resolver(body)) if selection_resolver else {"mode": "local"}

    @router.post(
        "/runs", response_model=RunSnapshot, status_code=202, operation_id="createResearchRun"
    )
    def create_run(
        body: CreateResearchRequest,
        idempotency_key: str | None = Header(default=None, max_length=160),
    ):
        service, scope = context()
        return guarded(
            lambda: service.create_run(
                body, **scope, idempotency_key=idempotency_key, manifest=selection(body)
            )
        )

    @router.get("/runs", response_model=RunPage, operation_id="listResearchRuns")
    def list_runs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=1000),
        status: RunStatus | None = None,
        ticker: str | None = Query(default=None, max_length=32),
    ):
        service, scope = context()
        return guarded(
            lambda: service.list_runs(
                **scope, limit=limit, cursor=cursor, status=status, ticker=ticker
            )
        )

    @router.get("/runs/{run_id}", response_model=RunSnapshot, operation_id="getResearchRun")
    def get_run(run_id: UUID):
        service, scope = context()
        return guarded(lambda: service.get_run(str(run_id), **scope))

    @router.post(
        "/runs/{run_id}/cancel", response_model=RunSnapshot, operation_id="cancelResearchRun"
    )
    def cancel_run(run_id: UUID):
        service, scope = context()
        return guarded(lambda: service.cancel_run(str(run_id), **scope))

    @router.post(
        "/runs/{run_id}/resume", response_model=RunSnapshot, operation_id="resumeResearchRun"
    )
    def resume_run(run_id: UUID):
        service, scope = context()
        return guarded(lambda: service.resume_run(str(run_id), **scope))

    @router.post(
        "/runs/{run_id}/clone",
        response_model=RunSnapshot,
        status_code=202,
        operation_id="cloneResearchRun",
    )
    def clone_run(
        run_id: UUID,
        body: CreateResearchRequest | None = None,
        idempotency_key: str | None = Header(default=None, max_length=160),
    ):
        service, scope = context()

        def clone():
            service.get_run(str(run_id), **scope)
            if body is not None:
                chosen = body
            else:
                return JSONResponse(
                    {"error_code": "rerun_review_required", "message": "请先确认研究日期、模型模板和资料，再提交完整研究请求", "retryable": False},
                    status_code=422,
                )
            return service.create_run(
                chosen, **scope, idempotency_key=idempotency_key, manifest=selection(chosen)
            )

        return guarded(clone)

    @router.get(
        "/runs/{run_id}/manifest",
        response_model=ManifestResponse,
        operation_id="getResearchManifest",
    )
    def manifest(run_id: UUID):
        service, scope = context()
        return guarded(lambda: service.get_manifest(str(run_id), **scope))

    @router.get(
        "/runs/{run_id}/event-log", response_model=RunEvents, operation_id="getResearchEvents"
    )
    def event_log(
        run_id: UUID,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        service, scope = context()
        result = guarded(
            lambda: service.list_events(str(run_id), **scope, after_seq=after_seq, limit=limit)
        )
        return result if isinstance(result, Response) else {"events": result}

    def exported_report(run_id, format):
        service, scope = context()
        result = guarded(lambda: service.report_file(str(run_id), format, **scope))
        if isinstance(result, Response):
            return result
        return FileResponse(
            result,
            media_type="text/html" if format == "html" else "text/markdown",
            filename=None if format == "html" else f"{run_id}.md",
        )

    @router.get(
        "/runs/{run_id}/report", response_class=FileResponse, operation_id="getResearchHtmlReport"
    )
    def html_report(run_id: UUID):
        return exported_report(run_id, "html")

    @router.get(
        "/runs/{run_id}/report.md",
        response_class=FileResponse,
        operation_id="getResearchMarkdownReport",
    )
    def markdown_report(run_id: UUID):
        return exported_report(run_id, "markdown")

    @router.get(
        "/runs/{run_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
        operation_id="streamResearchEvents",
    )
    async def stream_events(
        run_id: UUID,
        request: Request,
        cursor: str | None = Query(default=None, max_length=100),
        last_event_id: str | None = Header(default=None, max_length=100),
    ):
        service, scope = context()
        safe_id = str(run_id)
        checked = await run_in_threadpool(guarded, lambda: service.get_run(safe_id, **scope))
        if isinstance(checked, Response):
            return checked
        after = 0
        supplied = last_event_id if last_event_id is not None else cursor
        if supplied:
            try:
                cursor_run, raw_seq = supplied.rsplit(":", 1)
                if (
                    str(UUID(cursor_run)) != safe_id
                    or not raw_seq.isascii()
                    or not raw_seq.isdigit()
                ):
                    raise ValueError
                after = int(raw_seq)
                if after < 1:
                    raise ValueError
            except ValueError:
                return JSONResponse(
                    {
                        "error_code": "invalid_event_cursor",
                        "message": "事件游标无效",
                        "retryable": False,
                    },
                    status_code=422,
                )
            probe = await run_in_threadpool(
                guarded, lambda: service.list_events(safe_id, **scope, after_seq=after - 1, limit=1)
            )
            if isinstance(probe, Response):
                return probe
            if not probe or probe[0]["seq"] != after:
                return JSONResponse(
                    {
                        "error_code": "invalid_event_cursor",
                        "message": "事件游标已失效，请刷新状态",
                        "retryable": False,
                    },
                    status_code=422,
                )

        async def stream():
            nonlocal after
            last_ping = time.monotonic()
            while not await request.is_disconnected():
                batch = await run_in_threadpool(
                    guarded,
                    lambda after_seq=after: service.list_events(
                        safe_id, **scope, after_seq=after_seq, limit=200
                    ),
                )
                if isinstance(batch, Response):
                    return  # Authorization is checked on every poll; never leak an error payload.
                for event in batch:
                    envelope = EventEnvelope.model_validate(event)
                    encoded = json.dumps(
                        envelope.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                    )
                    yield f"id: {event['event_id']}\nevent: {event['type']}\ndata: {encoded}\n\n"
                    after = event["seq"]
                state = await run_in_threadpool(guarded, lambda: service.get_run(safe_id, **scope))
                if isinstance(state, Response):
                    return
                if state["status"] in terminal and len(batch) < 200:
                    tail = await run_in_threadpool(
                        guarded,
                        lambda after_seq=after: service.list_events(
                            safe_id, **scope, after_seq=after_seq, limit=1
                        ),
                    )
                    if isinstance(tail, Response) or not tail:
                        return
                    continue
                if time.monotonic() - last_ping >= heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    last_ping = time.monotonic()
                await asyncio.sleep(poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    return router
