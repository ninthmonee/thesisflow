"""Request-local identity with opaque cookie sessions and no ambient local-user fallback."""

from contextvars import ContextVar
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class AuthenticatedRequest:
    identity: object
    raw_token: str = field(repr=False)


_request_identity = ContextVar("tradingagents_request_identity", default=None)


def current_request_identity():
    return _request_identity.get()


class AccountAuthenticationMiddleware:
    """Hold identity through the complete ASGI response, including SSE streaming."""

    def __init__(self, app, *, authentication, service_registry=None):
        self.app, self.authentication = app, authentication
        self.service_registry = service_registry

    async def __call__(self, scope, receive, send):
        from tradingagents.web.application.authentication import SESSION_COOKIE, AuthError

        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        path, method = scope["path"], scope["method"]
        public = (method == "GET" and path in {"/api/v1/health", "/api/v1/auth/session"}) or (
            method == "POST" and path in {"/api/v1/auth/setup", "/api/v1/auth/register", "/api/v1/auth/login"}
        )
        if public:
            return await self.app(scope, receive, send)
        try:
            token = request.cookies.get(SESSION_COOKIE)
            identity = await run_in_threadpool(self.authentication.authenticate, token)
            expected_workspace = request.headers.get("x-workspace-id")
            if expected_workspace and expected_workspace != identity.workspace.workspace_id:
                raise AuthError("session_identity_changed", "登录账号已改变，请重新加载当前工作区。", 401)
            if method not in {"GET", "HEAD", "OPTIONS"}:
                await run_in_threadpool(self.authentication.validate_csrf, token, request.headers.get("x-csrf-token"))
        except AuthError as error:
            response = JSONResponse({"error_code": error.code, "message": error.message, "retryable": False},
                status_code=error.status_code, headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        context = _request_identity.set(AuthenticatedRequest(identity, token))
        lease = (
            self.service_registry.lease(identity.workspace)
            if self.service_registry and not path.startswith("/api/v1/auth/") else None
        )
        try:
            if lease:
                try:
                    await run_in_threadpool(lease.__enter__)
                except Exception as error:
                    if all(hasattr(error, name) for name in ("code", "message", "status_code")):
                        response = JSONResponse({"error_code": error.code, "message": error.message,
                            "retryable": error.status_code == 503}, status_code=error.status_code)
                        return await response(scope, receive, send)
                    raise
            try:
                await self.app(scope, receive, send)
            finally:
                if lease:
                    lease.__exit__(None, None, None)
        finally:
            _request_identity.reset(context)


class SessionBoundResearch:
    """Recheck the live session on each polling operation after a stream opens."""

    def __init__(self, service, authentication, context):
        self.service, self.authentication, self.context = service, authentication, context

    def __getattr__(self, name):
        operation = getattr(self.service, name)
        if not callable(operation):
            return operation

        def guarded(*args, **kwargs):
            identity = self.authentication.authenticate(self.context.raw_token, touch=False)
            if (identity.user_id, identity.workspace.workspace_id) != (
                self.context.identity.user_id, self.context.identity.workspace.workspace_id
            ):
                from tradingagents.web.application.authentication import AuthError
                raise AuthError("session_identity_changed", "登录账号已改变，请重新登录。", 401)
            return operation(*args, **kwargs)

        return guarded
