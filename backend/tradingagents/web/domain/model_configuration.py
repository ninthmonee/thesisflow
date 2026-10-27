"""Provider presets, wire protocols and bounded user-configurable parameters."""

import json
from urllib.parse import urlsplit

from tradingagents.web.api.model_schemas import ModelConfigurationError

PROVIDERS = (
    {
        "id": "openai",
        "name": "OpenAI",
        "default_endpoint": "https://api.openai.com/v1",
        "auth_types": ["api_key"],
        "discovery_supported": True,
        "description": "OpenAI API",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "default_endpoint": "https://api.deepseek.com/v1",
        "auth_types": ["api_key"],
        "discovery_supported": True,
        "description": "DeepSeek API",
    },
    {
        "id": "anthropic",
        "name": "Anthropic / Claude",
        "default_endpoint": "https://api.anthropic.com",
        "auth_types": ["api_key"],
        "discovery_supported": False,
        "description": "支持手动模型 ID；本版本未接入模型发现。",
    },
    {
        "id": "google",
        "name": "Google / Gemini",
        "default_endpoint": None,
        "auth_types": ["api_key"],
        "discovery_supported": False,
        "description": "支持手动模型 ID；使用原生 Google 客户端。",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "default_endpoint": "http://127.0.0.1:11434/v1",
        "auth_types": ["none", "api_key"],
        "discovery_supported": True,
        "description": "本机 Ollama 兼容接口。",
    },
    {
        "id": "openai_compatible",
        "name": "OpenAI 兼容接口",
        "default_endpoint": None,
        "auth_types": ["api_key", "none"],
        "discovery_supported": True,
        "description": "自定义兼容地址，模型能力需自行检测。",
    },
)

_PROTOCOLS = {
    "openai": ["auto", "openai_chat_completions", "openai_responses"],
    "deepseek": ["auto", "openai_chat_completions"],
    "anthropic": ["auto", "anthropic_messages"],
    "google": ["auto", "google_genai"],
    "ollama": ["auto", "openai_chat_completions"],
    "openai_compatible": ["auto", "openai_chat_completions", "openai_responses"],
    "custom": [
        "auto",
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "google_genai",
    ],
    "chatgpt": ["auto", "openai_responses"],
}
PROVIDERS = tuple(
    {
        **item,
        "auth_types": list(dict.fromkeys([*item["auth_types"], "headers"])),
        "supported_protocols": _PROTOCOLS[item["id"]],
        "default_protocol": "auto",
    }
    for item in PROVIDERS
) + (
    {
        "id": "custom",
        "name": "自定义模型服务",
        "default_endpoint": None,
        "auth_types": ["api_key", "headers", "none"],
        "discovery_supported": True,
        "description": "自定义厂商名称、地址、模型ID及明确协议；未实现的专有协议需要适配器。",
        "supported_protocols": _PROTOCOLS["custom"],
        "default_protocol": "openai_chat_completions",
    },
    {
        "id": "chatgpt",
        "name": "ChatGPT 本机订阅授权",
        "default_endpoint": "https://api.openai.com/v1",
        "auth_types": ["oauth_local"],
        "discovery_supported": False,
        "description": "复用本机CLI的OAuth授权；无需输入API Key，订阅权限和可用模型须分别检测。",
        "supported_protocols": _PROTOCOLS["chatgpt"],
        "default_protocol": "openai_responses",
    },
)


def resolve_protocol(provider: str, endpoint: str | None, requested: str = "auto") -> str:
    if requested not in _PROTOCOLS.get(provider, []):
        raise ModelConfigurationError(
            "unsupported_model_protocol", "该供应商预设不支持所选协议，请使用自定义模型服务。"
        )
    if requested != "auto":
        return requested
    if provider == "chatgpt":
        return "openai_responses"
    if provider == "anthropic":
        return "anthropic_messages"
    if provider == "google":
        return "google_genai"
    host = urlsplit(endpoint).hostname if endpoint else None
    if provider == "openai" and (
        not host or host == "api.openai.com" or host.endswith(".openai.com")
    ):
        return "openai_responses"
    return "openai_chat_completions"


ROLES = (
    ("market", "技术分析", "quick"),
    ("social", "社交情绪", "quick"),
    ("news", "新闻分析", "quick"),
    ("fundamentals", "基本面分析", "quick"),
    ("bull", "多头研究", "quick"),
    ("bear", "空头研究", "quick"),
    ("research_manager", "研究经理", "deep"),
    ("trader", "交易提案", "quick"),
    ("risk_aggressive", "激进风险观点", "quick"),
    ("risk_neutral", "中立风险观点", "quick"),
    ("risk_conservative", "保守风险观点", "quick"),
    ("portfolio_manager", "组合经理", "deep"),
)


def provider_definition(provider: str) -> dict:
    for item in PROVIDERS:
        if item["id"] == provider:
            return dict(item)
    raise ModelConfigurationError("unknown_provider", "不支持的模型供应商")


def _extra_body(value):
    blocked = {
        "model",
        "messages",
        "input",
        "tools",
        "tool_choice",
        "stream",
        "base_url",
        "endpoint",
        "api_key",
        "authorization",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "headers",
    }

    def walk(item, depth=0):
        if depth > 5:
            raise ValueError
        if isinstance(item, dict):
            if len(item) > 32 or any(
                not isinstance(key, str)
                or key.lower() in blocked
                or key.lower().endswith(("_api_key", "_token", "_password", "_secret"))
                for key in item
            ):
                raise ValueError
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 32:
                raise ValueError
            for child in item:
                walk(child, depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError

    try:
        if not isinstance(value, dict):
            raise ValueError
        walk(value)
        if len(json.dumps(value, allow_nan=False).encode()) > 4000:
            raise ValueError
    except (TypeError, ValueError):
        raise ModelConfigurationError(
            "invalid_parameters", "extra_body须为有界JSON，且不能覆盖模型、消息、工具或凭据。"
        ) from None


def validate_parameters(
    values: dict, provider: str | None = None, protocol: str | None = None
) -> dict:
    if provider == "chatgpt" and set(values) - {"timeout", "max_retries", "reasoning_effort"}:
        raise ModelConfigurationError(
            "invalid_parameters",
            "ChatGPT订阅连接仅支持timeout、max_retries和reasoning_effort；不支持采样或输出长度覆盖。",
        )
    numeric = {
        "temperature": (0, 2),
        "max_tokens": (1, 65536),
        "timeout": (1, 180),
        "max_retries": (0, 5),
        "top_p": (0, 1),
        "seed": (-2147483648, 2147483647),
    }
    text = {"reasoning_effort", "effort", "thinking_level"}
    provider_options = {
        "openai": {"reasoning_effort"},
        "chatgpt": {"reasoning_effort"},
        "anthropic": {"effort"},
        "google": {"thinking_level"},
    }
    for key, value in values.items():
        if key in numeric:
            low, high = numeric[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not low <= value <= high
            ):
                raise ModelConfigurationError("invalid_parameters", f"模型参数 {key} 超出允许范围")
            if key in {"max_tokens", "max_retries", "seed"} and not isinstance(value, int):
                raise ModelConfigurationError("invalid_parameters", f"模型参数 {key} 必须是整数")
            if key == "seed" and protocol not in {None, "openai_chat_completions", "google_genai"}:
                raise ModelConfigurationError("invalid_parameters", "所选协议未实现seed参数。")
        elif key in text:
            allowed = (
                {"reasoning_effort"}
                if protocol in {"openai_chat_completions", "openai_responses"}
                else {"effort"}
                if protocol == "anthropic_messages"
                else {"thinking_level"}
                if protocol == "google_genai"
                else set()
            )
            permitted = (
                allowed
                if provider in {"custom", "openai_compatible"}
                else provider_options.get(provider, set())
            )
            if provider is not None and key not in permitted:
                raise ModelConfigurationError("invalid_parameters", "当前供应商不支持该思考参数")
            if not isinstance(value, str) or value not in {
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
            }:
                raise ModelConfigurationError("invalid_parameters", f"模型参数 {key} 无效")
        elif key == "stop":
            stops = [value] if isinstance(value, str) else value
            if (
                protocol == "openai_responses"
                or not isinstance(stops, list)
                or not 1 <= len(stops) <= 4
                or any(not isinstance(item, str) or not item or len(item) > 1000 for item in stops)
            ):
                raise ModelConfigurationError(
                    "invalid_parameters", "stop须为1至4个非空字符串，所选协议须支持停止序列。"
                )
        elif key == "extra_body":
            if protocol not in {None, "openai_chat_completions", "openai_responses"}:
                raise ModelConfigurationError(
                    "invalid_parameters", "extra_body仅支持OpenAI兼容协议。"
                )
            _extra_body(value)
        else:
            raise ModelConfigurationError("invalid_parameters", "包含不支持的模型参数")
    return dict(values)
