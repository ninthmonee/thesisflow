"""Same-origin password authentication using opaque HttpOnly session cookies."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .auth_schemas import (
    AuthLoginRequest,
    AuthPasswordChange,
    AuthRegisterRequest,
    AuthRegistrationSettings,
    AuthSessionStatus,
    AuthSetupRequest,
)
from .schemas import ApiError

SESSION_COOKIE = "ta_session"
CSRF_HEADER = "x-csrf-token"


class AuthRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def redacted(request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse({"error_code": "invalid_auth_request",
                    "message": "输入不符合要求，请检查用户名、密码长度与必填字段。", "retryable": False}, status_code=422)

        return redacted


def create_auth_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["authentication"], route_class=AuthRoute,
        responses={code: {"model": ApiError} for code in (401, 403, 409, 422, 429, 503)})

    def service():
        if service_reader is None:
            raise HTTPException(503, "账号服务尚未初始化")
        return service_reader()

    def cookie(request):
        return request.cookies.get(SESSION_COOKIE)

    def client(request):
        # Forwarded headers are not trusted identity or rate-limit input.
        return request.client.host if request.client else "unknown"

    def csrf(request):
        service().validate_csrf(cookie(request), request.headers.get(CSRF_HEADER))

    def same_origin(request):
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.headers.get("origin") != expected:
            from tradingagents.web.application.authentication import AuthError
            raise AuthError("auth_origin_forbidden", "请从当前站点的登录页面提交。", 403)

    def guarded(operation):
        try:
            return operation()
        except Exception as error:
            if all(hasattr(error, name) for name in ("code", "message", "status_code")):
                headers = {"Retry-After": "60"} if error.status_code == 429 else None
                return JSONResponse({"error_code": error.code, "message": error.message,
                    "retryable": error.status_code in {429, 503}}, status_code=error.status_code,
                    headers=headers)
            raise

    def signed_in(result, request, response):
        from tradingagents.web.application.authentication import SESSION_SECONDS
        response.set_cookie(SESSION_COOKIE, result.token, max_age=SESSION_SECONDS,
            httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return result.status

    def cleared(status, request, response):
        response.delete_cookie(SESSION_COOKIE, httponly=True, secure=request.url.scheme == "https",
            samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return status

    @router.get("/auth/session", response_model=AuthSessionStatus, operation_id="getAuthSession")
    def session_status(request: Request, response: Response):
        def read():
            status = service().session_status(cookie(request))
            response.headers["Cache-Control"] = "no-store"
            if not status.authenticated and cookie(request):
                return cleared(status, request, response)
            return status
        return guarded(read)

    @router.post("/auth/setup", response_model=AuthSessionStatus, status_code=201,
        operation_id="setupOwnerAccount")
    def setup(body: AuthSetupRequest, request: Request, response: Response):
        def create():
            same_origin(request)
            result = service().setup_owner(body, client_key=client(request), replaced_token=cookie(request))
            return signed_in(result, request, response)
        return guarded(create)

    @router.post("/auth/register", response_model=AuthSessionStatus, status_code=201,
        operation_id="registerAccount")
    def register(body: AuthRegisterRequest, request: Request, response: Response):
        def create():
            same_origin(request)
            result = service().register(body, client_key=client(request), replaced_token=cookie(request))
            return signed_in(result, request, response)
        return guarded(create)

    @router.post("/auth/login", response_model=AuthSessionStatus, operation_id="loginAccount")
    def login(body: AuthLoginRequest, request: Request, response: Response):
        def create():
            same_origin(request)
            result = service().login(body, client_key=client(request), replaced_token=cookie(request))
            return signed_in(result, request, response)
        return guarded(create)

    @router.post("/auth/logout", response_model=AuthSessionStatus, operation_id="logoutAccount")
    def logout(request: Request, response: Response):
        def perform():
            same_origin(request)
            csrf(request)
            return cleared(service().logout(cookie(request)), request, response)
        return guarded(perform)

    @router.post("/auth/logout-all", response_model=AuthSessionStatus, operation_id="logoutAllSessions")
    def logout_all(request: Request, response: Response):
        def perform():
            same_origin(request)
            csrf(request)
            return cleared(service().logout(cookie(request), all_sessions=True), request, response)
        return guarded(perform)

    @router.post("/auth/password", response_model=AuthSessionStatus, operation_id="changeAccountPassword")
    def password(body: AuthPasswordChange, request: Request, response: Response):
        def perform():
            same_origin(request)
            csrf(request)
            return signed_in(service().change_password(cookie(request), body), request, response)
        return guarded(perform)

    @router.get("/auth/registration", response_model=AuthRegistrationSettings,
        operation_id="getRegistrationSettings")
    def registration(request: Request):
        return guarded(lambda: service().registration_settings(service().authenticate(cookie(request))))

    @router.patch("/auth/registration", response_model=AuthRegistrationSettings,
        operation_id="updateRegistrationSettings")
    def update_registration(body: AuthRegistrationSettings, request: Request):
        def perform():
            same_origin(request)
            csrf(request)
            return service().update_registration(service().authenticate(cookie(request)), body)
        return guarded(perform)

    return router
