"""Owner-scoped investment rules and manual plans; no financial side effects."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .investment_schemas import (
    InvestmentPlanList,
    InvestmentPlanUpdate,
    InvestmentPlanView,
    InvestmentPlanWrite,
    InvestmentPolicyUpdate,
    InvestmentPolicyView,
)
from .schemas import ApiError


def create_investment_router(service_reader=None, *, prefix="/api/v1"):
    router = APIRouter(prefix=prefix, tags=["investment-system"],
        responses={status: {"model": ApiError} for status in (403, 404, 409, 422, 503)})

    def call(method, *args, **kwargs):
        if service_reader is None:
            raise HTTPException(503, "投资体系服务尚未初始化")
        try:
            return getattr(service_reader(), method)(*args, **kwargs)
        except Exception as error:
            if all(hasattr(error, field) for field in ("code", "message", "status_code")):
                return JSONResponse({"error_code": error.code, "message": error.message, "retryable": False}, status_code=error.status_code)
            raise

    @router.get("/investment-policy", response_model=InvestmentPolicyView, operation_id="getInvestmentPolicy")
    def policy():
        return call("get_policy")

    @router.put("/investment-policy", response_model=InvestmentPolicyView, operation_id="updateInvestmentPolicy")
    def update_policy(body: InvestmentPolicyUpdate):
        return call("update_policy", body)

    @router.get("/investment-plans", response_model=InvestmentPlanList, operation_id="listInvestmentPlans")
    def plans(ticker: str | None = Query(default=None, max_length=32), include_archived: bool = False):
        return call("list_plans", ticker=ticker, include_archived=include_archived)

    @router.post("/investment-plans", response_model=InvestmentPlanView, status_code=201, operation_id="createInvestmentPlan")
    def create_plan(body: InvestmentPlanWrite):
        return call("create_plan", body)

    @router.get("/investment-plans/{plan_id}", response_model=InvestmentPlanView, operation_id="getInvestmentPlan")
    def plan(plan_id: UUID):
        return call("get_plan", str(plan_id))

    @router.patch("/investment-plans/{plan_id}", response_model=InvestmentPlanView, operation_id="updateInvestmentPlan")
    def update_plan(plan_id: UUID, body: InvestmentPlanUpdate):
        return call("update_plan", str(plan_id), body)

    return router
