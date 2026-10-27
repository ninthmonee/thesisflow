"""Resolve explicit wire protocols without exposing authentication in snapshots."""

from copy import deepcopy

from pydantic import SecretStr

from tradingagents.web.api.model_schemas import ModelConfigurationError, validated_headers
from tradingagents.web.domain.model_configuration import resolve_protocol


def header_values(value) -> dict[str, str]:
    if value is None:
        return {}
    try:
        if not isinstance(value, dict):
            raise ValueError
        protected = {
            key: item if isinstance(item, SecretStr) else SecretStr(item)
            for key, item in value.items()
        }
        validated_headers(protected)
        return {key: item.get_secret_value() for key, item in protected.items()}
    except (TypeError, ValueError):
        raise ModelConfigurationError(
            "invalid_custom_headers", "自定义请求头格式无效；值不会回显。"
        ) from None


def client_arguments(
    snapshot: dict, secret: str | None, headers: dict[str, str], *, callbacks=None
) -> tuple[str, dict]:
    protocol = snapshot.get("api_protocol") or resolve_protocol(
        snapshot["provider"], snapshot.get("base_url")
    )
    parameters = deepcopy(snapshot.get("parameters", {}))
    if callbacks is not None:
        parameters["callbacks"] = callbacks
    if snapshot["provider"] == "chatgpt":
        if (
            secret
            or headers
            or protocol != "openai_responses"
            or snapshot.get("auth_type") != "oauth_local"
            or snapshot.get("base_url") != "https://api.openai.com/v1"
            or snapshot.get("credential_ref")
            or snapshot.get("extra_header_ref")
        ):
            raise ModelConfigurationError("invalid_credential", "ChatGPT连接仅使用本机OAuth授权。")
        identity = snapshot.get("local_authorization_fingerprint")
        if not isinstance(identity, str) or len(identity) != 64:
            raise ModelConfigurationError(
                "local_authorization_required", "请先完成并检测本机ChatGPT授权。", 409
            )
        parameters["chatgpt_authorization_fingerprint"] = identity
        return "chatgpt", parameters
    parameters["api_key"] = secret or "explicit-keyless-connection"
    if protocol in {"openai_chat_completions", "openai_responses"}:
        provider = (
            snapshot["provider"]
            if snapshot["provider"] in {"openai", "deepseek", "ollama", "openai_compatible"}
            else "openai_compatible"
        )
        parameters["use_responses_api"] = protocol == "openai_responses"
        if headers:
            parameters["default_headers"] = deepcopy(headers)
    elif protocol == "anthropic_messages":
        provider = "anthropic"
        if "stop" in parameters:
            stops = parameters.pop("stop")
            parameters["stop_sequences"] = [stops] if isinstance(stops, str) else stops
        if headers:
            parameters["default_headers"] = deepcopy(headers)
        if snapshot["provider"] == "custom":
            parameters["allow_custom_model_parameters"] = True
    elif protocol == "google_genai":
        provider = "google"
        if snapshot["provider"] == "custom":
            parameters["allow_custom_model_parameters"] = True
        if "max_tokens" in parameters:
            parameters["max_output_tokens"] = parameters.pop("max_tokens")
        if headers:
            parameters["additional_headers"] = deepcopy(headers)
    else:
        raise ModelConfigurationError("unsupported_model_protocol", "此模型协议尚未实现适配器。")
    return provider, parameters
