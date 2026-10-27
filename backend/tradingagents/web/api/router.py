"""M0 read-only v1 API plus deterministic schema-only application factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic.json_schema import models_json_schema

from .alternative_router import create_alternative_router
from .auth_router import create_auth_router
from .data_router import create_data_router
from .intelligence_router import create_intelligence_router
from .market_router import create_market_router
from .model_router import create_model_router
from .model_schemas import ModelServiceProtocol
from .professional_pulse_router import create_professional_pulse_router
from .readiness_router import create_readiness_router
from .research_context_router import create_research_context_router
from .research_data_router import create_research_data_router
from .research_evidence_router import create_research_evidence_router
from .research_router import create_research_router
from .schemas import (
    CONTRACT_MODELS,
    ApplicationInfo,
    BootstrapResponse,
    Capability,
    CapabilityState,
    HealthResponse,
    ModelStatus,
    ResearchDefaults,
    ServiceStatus,
    WorkspaceInfo,
)
from .themes_router import create_themes_router
from .tracking_router import create_tracking_router
from .viewpoint_router import create_viewpoint_router
from .watchlists import create_watchlist_router
from .workflow_router import create_workflow_router

PublicConfigReader = Callable[[], Mapping[str, Any]]
WorkspaceReader = Callable[[], Mapping[str, Any]]


def _string(value: Any, default: str = "unknown") -> str:
    return value if isinstance(value, str) and value else default


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def build_bootstrap(
    public: Mapping[str, Any],
    workspace: Mapping[str, Any] | None = None,
    capability_states: Mapping[str, CapabilityState] | None = None,
) -> BootstrapResponse:
    """Pick allowlisted presentation fields; never serialize the input mapping."""
    configured = public.get("model_ready") is True
    provider = _string(public.get("provider"))
    space = workspace or {}
    language = "English" if public.get("output_language") == "English" else "Chinese"
    services = []
    for item in public.get("services", []):
        if not isinstance(item, Mapping):
            continue
        services.append(
            ServiceStatus(
                id=_string(item.get("id")),
                name=_string(item.get("name")),
                configured=item.get("configured") is True,
                status=_string(item.get("status")),
            )
        )
    capabilities = [
        Capability(
            id="research",
            label="TradingAgents 个股研究",
            state="available" if configured else "configuration_required",
            reason="现有研究链路；凭据存在性检查不代表模型调用已验证。"
            if configured
            else "需要配置模型凭据或完成本机授权。",
        ),
        Capability(
            id="model_connections",
            label="模型连接管理",
            state="planned",
            reason="当前为只读模型状态；可编辑连接将在 M1 实现。",
        ),
        Capability(
            id="watchlists",
            label="自选股管理",
            state="planned",
            reason="将在 M1 接入持久化自选列表。",
        ),
        Capability(
            id="charts",
            label="专业行情工作台",
            state="planned",
            reason="将在 M2 接入带来源和时间口径的真实行情。",
        ),
        Capability(
            id="market_pulse",
            label="市场脉搏",
            state="planned",
            reason="将在 M2 接入真实波动、广度及市场简报。",
        ),
        Capability(
            id="sector_attention",
            label="板块与资金关注",
            state="planned",
            reason="将在 M2 接入行业强弱与同 feed 成交关注。",
        ),
    ]
    for capability in capabilities:
        if capability_states and capability.id in capability_states:
            capability.state = capability_states[capability.id]
            capability.reason = None
    return BootstrapResponse(
        application=ApplicationInfo(version=_string(public.get("version"))),
        workspace=WorkspaceInfo(
            id=_string(space.get("id"), "local-workspace"),
            name=_string(space.get("name"), "本机工作区"),
            owner_id=_string(space.get("owner_id"), "local-user"),
        ),
        today=_optional_string(public.get("today")),
        defaults=ResearchDefaults(language=language),
        model=ModelStatus(
            configured=configured,
            provider=provider,
            quick_provider=_string(public.get("quick_provider"), provider),
            deep_provider=_string(public.get("deep_provider"), provider),
            quick_model=_optional_string(public.get("quick_model")),
            deep_model=_optional_string(public.get("deep_model")),
            status="configured" if configured else "missing",
        ),
        services=services,
        capabilities=capabilities,
        active_run_id=_optional_string(public.get("active_run_id")),
    )


def create_api_router(
    *,
    public_config_reader: PublicConfigReader | None = None,
    workspace_reader: WorkspaceReader | None = None,
    capability_states: Mapping[str, CapabilityState] | None = None,
    model_service_reader: Callable[[], ModelServiceProtocol] | None = None,
    watchlist_service_reader: Callable | None = None,
    research_service_reader: Callable | None = None,
    scope_reader: Callable | None = None,
    selection_resolver: Callable | None = None,
    market_service_reader: Callable | None = None,
    intelligence_service_reader: Callable | None = None,
    brief_generator_reader: Callable | None = None,
    data_service_reader: Callable | None = None,
    themes_service_reader: Callable | None = None,
    alternative_service_reader: Callable | None = None,
    research_data_service_reader: Callable | None = None,
    tracking_service_reader: Callable | None = None,
    workflow_reader: Callable | None = None,
    research_evidence_service_reader: Callable | None = None,
    readiness_service_reader: Callable | None = None,
    research_context_service_reader: Callable | None = None,
    viewpoint_service_reader: Callable | None = None,
    professional_pulse_service_reader: Callable | None = None,
    authentication_service_reader: Callable | None = None,
) -> APIRouter:
    """Readers are injected at runtime; schema export supplies none and performs no IO."""
    router = APIRouter(prefix="/api/v1", tags=["system"])
    router.include_router(create_auth_router(authentication_service_reader, prefix=""))

    @router.get("/health", response_model=HealthResponse, operation_id="getHealth")
    def health() -> HealthResponse:
        return HealthResponse()

    @router.get("/bootstrap", response_model=BootstrapResponse, operation_id="getBootstrap")
    def bootstrap() -> BootstrapResponse:
        public = public_config_reader() if public_config_reader else {}
        workspace = workspace_reader() if workspace_reader else None
        return build_bootstrap(public, workspace, capability_states)

    router.include_router(create_model_router(model_service_reader, prefix=""))
    router.include_router(create_watchlist_router(watchlist_service_reader, prefix=""))
    router.include_router(
        create_research_router(research_service_reader, scope_reader, selection_resolver, prefix="")
    )
    router.include_router(create_market_router(market_service_reader, prefix=""))
    router.include_router(
        create_intelligence_router(intelligence_service_reader, brief_generator_reader, prefix="")
    )
    router.include_router(create_data_router(data_service_reader, prefix=""))
    router.include_router(create_themes_router(themes_service_reader, prefix=""))
    router.include_router(create_alternative_router(alternative_service_reader, prefix=""))
    router.include_router(create_research_data_router(research_data_service_reader, prefix=""))
    router.include_router(create_tracking_router(tracking_service_reader, prefix=""))
    router.include_router(create_workflow_router(workflow_reader, prefix=""))
    router.include_router(
        create_research_evidence_router(research_evidence_service_reader, scope_reader, prefix="")
    )
    router.include_router(create_readiness_router(readiness_service_reader, prefix=""))
    router.include_router(create_research_context_router(research_context_service_reader, prefix=""))
    router.include_router(create_viewpoint_router(viewpoint_service_reader, prefix=""))
    router.include_router(create_professional_pulse_router(professional_pulse_service_reader, prefix=""))
    return router


def register_contract_models(app: FastAPI) -> None:
    """Register event/run definitions without inventing unimplemented HTTP operations."""
    original_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original_openapi()
        _, definitions = models_json_schema(
            [(model, "validation") for model in CONTRACT_MODELS],
            ref_template="#/components/schemas/{model}",
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(
            definitions.get("$defs", {})
        )
        schema["x-tradingagents-event-schema"] = {"$ref": "#/components/schemas/EventEnvelope"}
        return schema

    app.openapi = openapi


def create_schema_app() -> FastAPI:
    """Pure contract export: no store, worker, default config, credentials, or network."""
    app = FastAPI(
        title="TradingAgents Web API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(create_api_router())
    register_contract_models(app)
    return app
