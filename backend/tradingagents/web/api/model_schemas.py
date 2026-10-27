"""Model-configuration API DTOs; this module has no storage or vendor imports."""

from datetime import datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from .schemas import Analyst, ContractModel, Language

RoleId = Literal[
    "market",
    "social",
    "news",
    "fundamentals",
    "bull",
    "bear",
    "research_manager",
    "trader",
    "risk_aggressive",
    "risk_neutral",
    "risk_conservative",
    "portfolio_manager",
]
ProviderId = Literal[
    "openai", "deepseek", "anthropic", "google", "ollama", "openai_compatible", "custom", "chatgpt"
]
ModelProtocol = Literal[
    "auto", "openai_chat_completions", "openai_responses", "anthropic_messages", "google_genai"
]
AuthType = Literal["api_key", "headers", "none", "oauth_local"]


def validated_headers(value: dict[str, SecretStr] | None) -> dict[str, SecretStr] | None:
    if value is None:
        return None
    import re

    blocked = {
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "cookie",
        "set-cookie",
        "origin",
        "referer",
    }
    if len(value) > 16:
        raise ValueError("最多支持16个自定义请求头")
    seen = set()
    for key, secret in value.items():
        if (
            not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}", key)
            or key.lower() in blocked
            or key.lower() in seen
        ):
            raise ValueError("自定义请求头名称无效或重复")
        seen.add(key.lower())
        raw = secret.get_secret_value()
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw) > 4096
            or any(char in raw for char in "\r\n\x00")
        ):
            raise ValueError("自定义请求头值无效")
    return value


class ModelConfigurationError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def validated_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("接口地址端口无效") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("接口地址必须是 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接口地址不能包含账号、密码、查询参数或片段")
    return value.strip().rstrip("/")


class LocalAuthorizationView(ContractModel):
    status: Literal["authorized", "not_authorized", "unavailable"]
    message: str
    login_command: str
    logout_command: str


class ProviderView(ContractModel):
    id: ProviderId
    name: str
    default_endpoint: str | None
    auth_types: list[AuthType]
    discovery_supported: bool
    description: str
    supported_protocols: list[ModelProtocol] = Field(default_factory=list)
    default_protocol: ModelProtocol = "auto"
    local_authorization: LocalAuthorizationView | None = None


class ProviderList(ContractModel):
    providers: list[ProviderView]


class ConnectionCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    provider: ProviderId
    auth_type: AuthType = "api_key"
    api_key: SecretStr | None = None
    vendor_name: str | None = Field(default=None, min_length=1, max_length=120)
    api_protocol: ModelProtocol = "auto"
    extra_headers: dict[str, SecretStr] | None = None
    endpoint: str | None = Field(default=None, max_length=1000)
    scope: Literal["private", "workspace"] = "private"
    enabled: bool = True

    _endpoint = field_validator("endpoint")(validated_endpoint)
    _headers = field_validator("extra_headers")(validated_headers)


class ConnectionUpdate(ContractModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: SecretStr | None = None
    auth_type: AuthType | None = None
    vendor_name: str | None = Field(default=None, min_length=1, max_length=120)
    api_protocol: ModelProtocol | None = None
    extra_headers: dict[str, SecretStr] | None = None
    clear_extra_headers: bool = False
    endpoint: str | None = Field(default=None, max_length=1000)
    scope: Literal["private", "workspace"] | None = None
    enabled: bool | None = None

    _endpoint = field_validator("endpoint")(validated_endpoint)
    _headers = field_validator("extra_headers")(validated_headers)


class ConnectionView(ContractModel):
    id: str
    name: str
    provider: str
    auth_type: str
    vendor_name: str
    api_protocol: ModelProtocol
    resolved_protocol: ModelProtocol
    headers_configured: bool
    header_names: list[str]
    endpoint: str | None
    scope: str
    enabled: bool
    credential_configured: bool
    credential_mask: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    local_authorization: LocalAuthorizationView | None = None


class ConnectionList(ContractModel):
    connections: list[ConnectionView]


class ProfileCreate(ContractModel):
    connection_id: str
    model_id: str = Field(min_length=1, max_length=240, pattern=r"^[^\s]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(ContractModel):
    revision: int = Field(ge=1)
    model_id: str | None = Field(default=None, min_length=1, max_length=240, pattern=r"^[^\s]+$")
    parameters: dict[str, Any] | None = None


class ProfileView(ContractModel):
    id: str
    connection_id: str
    connection_name: str
    provider: str
    model_id: str
    parameters: dict[str, Any]
    capabilities: dict[str, Any]
    test_status: str
    tested_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ProfileList(ContractModel):
    profiles: list[ProfileView]


class RevisionRequest(ContractModel):
    revision: int = Field(ge=1)


class ModelTestResult(ContractModel):
    profile: ProfileView
    success: bool
    message: str


class DiscoveredModel(ContractModel):
    id: str


class ModelDiscovery(ContractModel):
    supported: bool
    models: list[DiscoveredModel] = Field(default_factory=list)
    message: str


class RoleView(ContractModel):
    id: RoleId
    label: str
    tier: Literal["quick", "deep"]


class TemplateWrite(ContractModel):
    expected_version: int = Field(default=0, ge=0)
    name: str = Field(default="默认研究配置", min_length=1, max_length=120)
    default_profile_id: str
    role_bindings: dict[RoleId, str] = Field(default_factory=dict)
    analysts: list[Analyst] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"],
        min_length=1,
        max_length=4,
    )
    depth: Literal[1, 2, 3] = 1
    language: Language = "Chinese"


class TemplateView(ContractModel):
    id: str
    template_id: str
    version: int
    name: str
    default_profile_id: str
    role_bindings: dict[str, str]
    analysts: list[Analyst]
    depth: Literal[1, 2, 3]
    language: Language
    created_at: datetime


class ModelSettings(ContractModel):
    roles: list[RoleView]
    default_template: TemplateView | None


class TemplateList(ContractModel):
    templates: list[TemplateView]


class ModelServiceProtocol(Protocol):
    def providers(self) -> list[dict]: ...
    def list_connections(self) -> list[dict]: ...
    def create_connection(self, values: dict) -> dict: ...
    def update_connection(self, connection_id: str, values: dict) -> dict: ...
    def delete_connection(self, connection_id: str, revision: int) -> None: ...
    def discover_models(self, connection_id: str) -> dict: ...
    def list_profiles(self) -> list[dict]: ...
    def create_profile(self, values: dict) -> dict: ...
    def update_profile(self, profile_id: str, values: dict) -> dict: ...
    def test_profile(self, profile_id: str, revision: int) -> dict: ...
    def settings(self) -> dict: ...
    def save_settings(self, values: dict) -> dict: ...
    def list_templates(self) -> list[dict]: ...
