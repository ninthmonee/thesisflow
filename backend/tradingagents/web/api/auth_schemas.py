"""Side-effect-free registration and session contracts; secrets never serialize."""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr, field_validator

from .base import ContractModel

RegistrationMode = Literal["open", "closed"]


class AuthWorkspace(ContractModel):
    id: str
    name: str
    owner_id: str


class AuthUser(ContractModel):
    id: str
    username: str
    display_name: str
    is_admin: bool
    workspace: AuthWorkspace


class AuthSessionStatus(ContractModel):
    authenticated: bool
    setup_required: bool
    registration_mode: RegistrationMode
    user: AuthUser | None = None
    csrf_token: str | None = None
    expires_at: datetime | None = None


class AuthLoginRequest(ContractModel):
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        if not isinstance(value, str):
            raise ValueError("用户名须为文本")
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", value):
            raise ValueError("用户名须为3–32位字母、数字、点、下划线或短横线")
        return value


class AuthRegisterRequest(AuthLoginRequest):
    password: SecretStr = Field(min_length=12, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator("password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("密码不能全为空白")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value):
        if value is None:
            return None
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("显示名称不能包含控制字符")
        return value or None


class AuthSetupRequest(AuthRegisterRequest):
    setup_token: SecretStr = Field(min_length=32, max_length=128)


class AuthPasswordChange(ContractModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("密码不能全为空白")
        return value


class AuthRegistrationSettings(ContractModel):
    registration_mode: RegistrationMode
