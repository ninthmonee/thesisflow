"""Pure contracts for explicitly owned local Agent runtime connections."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import ContractModel

RuntimeAuthMode = Literal["runtime_login", "model_connection"]
RuntimeReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]


class RuntimeConnectionCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    runtime_type: Literal["codex"] = "codex"
    executable_path: str | None = Field(default=None, max_length=1000)
    model_id: str = Field(min_length=1, max_length=240, pattern=r"^[^\s]+$")
    reasoning_effort: RuntimeReasoningEffort | None = None
    enabled: bool = False
    auth_mode: RuntimeAuthMode = "runtime_login"
    model_profile_id: str | None = Field(default=None, max_length=36)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("请输入有效连接名称")
        return value

    @field_validator("executable_path")
    @classmethod
    def executable_text(cls, value):
        if value is None or not value.strip():
            return None
        if any(char in value for char in "\r\n\x00"):
            raise ValueError("程序路径无效")
        return value.strip()

    @field_validator("model_id")
    @classmethod
    def model_text(cls, value):
        if any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise ValueError("模型ID不能包含空白或控制字符")
        return value

    @model_validator(mode="after")
    def binding(self):
        if self.auth_mode == "model_connection" and not self.model_profile_id:
            raise ValueError("请选择当前账号的模型配置")
        if self.auth_mode == "runtime_login" and self.model_profile_id:
            raise ValueError("本机登录模式不应绑定API模型配置")
        return self


class RuntimeConnectionUpdate(ContractModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    executable_path: str | None = Field(default=None, max_length=1000)
    model_id: str | None = Field(default=None, min_length=1, max_length=240, pattern=r"^[^\s]+$")
    reasoning_effort: RuntimeReasoningEffort | None = None
    enabled: bool | None = None
    auth_mode: RuntimeAuthMode | None = None
    model_profile_id: str | None = Field(default=None, max_length=36)


class RuntimeCapabilities(ContractModel):
    dynamic_tools: bool = False
    cancel: bool = False
    resume: bool = False
    model_discovery: bool = False


class RuntimeModel(ContractModel):
    id: str


class RuntimeConnectionView(ContractModel):
    id: str
    name: str
    runtime_type: Literal["codex"]
    transport: Literal["stdio"] = "stdio"
    execution_host: Literal["server_local"] = "server_local"
    executable_path: str | None
    model_id: str
    reasoning_effort: RuntimeReasoningEffort | None
    enabled: bool
    auth_mode: RuntimeAuthMode
    model_profile_id: str | None
    login_command: str | None = None
    revision: int
    readiness: Literal["not_checked", "ready", "failed", "stale"]
    program_version: str | None = None
    auth_status: Literal["authorized", "not_authorized", "unknown"] = "unknown"
    capabilities: RuntimeCapabilities = Field(default_factory=RuntimeCapabilities)
    models: list[RuntimeModel] = Field(default_factory=list)
    message: str
    checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RuntimeConnectionList(ContractModel):
    connections: list[RuntimeConnectionView]


class RuntimeTestResult(ContractModel):
    connection: RuntimeConnectionView
    success: bool
    message: str


class RuntimeType(ContractModel):
    id: Literal["codex", "claude_code", "opencode"]
    name: str
    implemented: bool
    host_access: bool
    transport: str
    message: str


class RuntimeTypeList(ContractModel):
    types: list[RuntimeType]


class RuntimeConfigurationError(Exception):
    def __init__(self, code, message, status_code=400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
