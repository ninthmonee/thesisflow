"""Write-only research credentials and public, versioned connection metadata."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from .schemas import ContractModel

ResearchDataProvider = Literal["fred", "alpha_vantage", "typesafe_jev", "sec_edgar"]
CredentialSource = Literal["keychain", "legacy_environment"]


class ResearchDataConnectionError(Exception):
    def __init__(self, code, message, status_code=400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def validated_jev_url(value):
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        canonical = value.rstrip("/")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or canonical not in {"https://api.typesafe.ai", "https://openrouter.ai/api"}
        ):
            raise ValueError
        return canonical
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Jev地址仅支持已适配的官方HTTPS入口，不能包含查询参数或账号密码") from None


class ResearchDataConnectionCreate(ContractModel):
    provider: ResearchDataProvider
    name: str = Field(min_length=1, max_length=120)
    credential_source: CredentialSource = "keychain"
    api_key: SecretStr | None = None
    user_agent: SecretStr | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def public_url(cls, value):
        return validated_jev_url(value)


class ResearchDataConnectionUpdate(ContractModel):
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    credential_source: CredentialSource | None = None
    api_key: SecretStr | None = None
    user_agent: SecretStr | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def public_url(cls, value):
        return validated_jev_url(value)


class ResearchDataConnectionRevision(ContractModel):
    revision: int = Field(ge=1, strict=True)


class ResearchDataConnectionView(ContractModel):
    id: str
    name: str
    provider: ResearchDataProvider
    credential_source: CredentialSource
    enabled: bool
    revision: int
    credential_configured: bool
    user_agent_configured: bool
    model: str | None
    base_url: str | None
    test_status: Literal["untested", "passed", "failed"]
    tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchDataConnectionList(ContractModel):
    connections: list[ResearchDataConnectionView]


class ResearchDataConnectionTestResponse(ContractModel):
    connection: ResearchDataConnectionView
    success: bool
    message: str
