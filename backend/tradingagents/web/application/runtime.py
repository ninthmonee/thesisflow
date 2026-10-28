"""Resolve frozen research manifests into private runtime-only graph configuration."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.domain.model_access import is_local_owner
from tradingagents.web.infrastructure.database import Database
from tradingagents.web.infrastructure.recovery_policy import requires_explicit_model_binding
from tradingagents.web.infrastructure.secrets import SecretStore
from tradingagents.web.infrastructure.seeds import LocalWorkspace


def create_selection_resolver(
    model_service: ModelConfigurationService,
    local_status_reader: Callable[[], Mapping[str, Any]] | None = None,
):
    """Explicit templates fail explicitly; only absent settings use existing local auth."""

    def resolve(request):
        settings = model_service.settings()
        if request.template_version_id or settings["default_template"] is not None:
            return {
                "mode": "configured",
                "model_snapshot": model_service.resolve_model_snapshot(
                    request.template_version_id, require_tested=True, analysts=request.analysts
                ),
            }
        if not is_local_owner(model_service.workspace.workspace_id, model_service.workspace.user_id):
            raise ModelConfigurationError(
                "model_connection_required", "请先在当前账号保存并检测自己的模型连接；不会使用服务器的本机账号。", 409
            )
        if requires_explicit_model_binding(model_service.database):
            raise ModelConfigurationError(
                "recovery_credentials_rebind_required", "恢复的数据需要在模型设置中显式绑定并检测连接；不会继承本机环境或订阅账号。", 409
            )
        if local_status_reader is not None and not local_status_reader().get("model_ready"):
            raise ModelConfigurationError(
                "models_not_configured", "请先配置模型或完成本机登录授权。", 409
            )
        return {"mode": "local", "configuration_source": "existing_local_settings"}

    return resolve


def create_runtime_resolver(
    database: Database,
    base_config: Mapping[str, Any] | None = None,
    secret_store: SecretStore | None = None,
):
    """Factory used by the worker; API export never imports this module."""
    from tradingagents.web.application.research import SAFE_RUNTIME_KEYS

    if base_config is None:
        from tradingagents.default_config import build_default_config

        baseline = build_default_config()
    else:
        baseline = deepcopy(dict(base_config))

    def resolve(manifest: Mapping[str, Any], *, callbacks=None) -> dict:
        selection = manifest.get("selection", {})
        external = selection.get("execution_mode") == "local_agent"
        workspace = LocalWorkspace(
            user_id=manifest["owner_id"],
            workspace_id=manifest["workspace_id"],
            watchlist_id="",
            name="研究工作区",
        )
        if "model_snapshot" not in selection and not is_local_owner(workspace.workspace_id, workspace.user_id):
            raise ModelConfigurationError(
                "local_model_access_forbidden", "研究没有本账号的模型快照，不能回退到服务器本机账号。", 403
            )
        # Configured users receive only public execution defaults. Private host
        # endpoints, keys, headers and local paths never become their graph config.
        config = (
            {key: deepcopy(value) for key, value in baseline.items() if key in SAFE_RUNTIME_KEYS}
            if "model_snapshot" in selection or external else deepcopy(baseline)
        )
        frozen = manifest.get("runtime", {})
        config.update(
            {key: deepcopy(value) for key, value in frozen.items() if key in SAFE_RUNTIME_KEYS}
        )
        request = manifest.get("request", {})
        config.update(
            output_language=request.get("language", "Chinese"),
            max_debate_rounds=request.get("depth", 1),
            max_risk_discuss_rounds=request.get("depth", 1),
            checkpoint_enabled=bool(request.get("checkpoint", True)),
        )
        if not external and "model_snapshot" not in selection and requires_explicit_model_binding(database):
            raise ModelConfigurationError(
                "recovery_credentials_rebind_required", "恢复的旧本机授权任务不能自动复用账号，请配置模型模板后重新研究。", 409
            )
        from tradingagents.web.api.research_data_schemas import ResearchDataConnectionError
        from tradingagents.web.application.research_data_connections import (
            ResearchDataConnectionService,
        )

        data_service = ResearchDataConnectionService(database, workspace, secret_store=secret_store)
        frozen_data = manifest.get("data_connections")
        if frozen_data is None:
            frozen_data = data_service.freeze_for_run(workspace.workspace_id, workspace.user_id)
            if any(entry.get("connection_id") for entry in frozen_data["providers"].values()):
                raise ResearchDataConnectionError(
                    "research_data_configuration_changed", "旧研究未冻结数据配置，请重新研究", 409
                )
        config["__data_connection_resolver"] = data_service.build_runtime_resolver(frozen_data)
        if external:
            return config
        if "model_snapshot" in selection:
            snapshot = deepcopy(selection["model_snapshot"])
            if (
                not isinstance(snapshot, Mapping)
                or not isinstance(snapshot.get("roles"), Mapping)
                or not isinstance(snapshot.get("signature"), str)
            ):
                raise ModelConfigurationError(
                    "invalid_model_selection", "运行模型快照不完整，不能回退到其他连接。", 409
                )
            workspace = LocalWorkspace(
                user_id=manifest["owner_id"],
                workspace_id=manifest["workspace_id"],
                watchlist_id="",
                name="研究工作区",
            )
            service = ModelConfigurationService(database, workspace, secret_store=secret_store)
            config["__role_model_resolver"] = service.build_role_model_resolver(
                snapshot, callbacks=callbacks
            )
            config["__role_model_signature"] = snapshot["signature"]
        elif selection.get("mode") not in {None, "local"}:
            raise ModelConfigurationError(
                "invalid_model_selection", "运行模型配置缺失，不能回退到其他连接。", 409
            )
        return config

    return resolve
