"""Public data-center contracts; importing this module initializes no providers."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field

from .market_schemas import MarketCapabilities
from .schemas import ContractModel


class DataSettingsError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class DataRuntimeSettings(ContractModel):
    minute_cache_ttl_seconds: int = Field(default=30, ge=5, le=300, strict=True)
    daily_cache_ttl_seconds: int = Field(default=180, ge=30, le=3600, strict=True)
    aggregation_max_workers: int = Field(default=4, ge=1, le=8, strict=True)
    aggregation_timeout_seconds: int = Field(default=15, ge=5, le=60, strict=True)
    aggregation_refresh_seconds: int = Field(default=900, ge=30, le=3600, strict=True)


class DataSettingsUpdate(ContractModel):
    revision: int = Field(ge=0, strict=True)
    settings: DataRuntimeSettings


class DataSettingsResponse(ContractModel):
    revision: int
    settings: DataRuntimeSettings
    updated_at: datetime | None
    activation: Literal["active", "saved_pending", "restart_required"]
    scope: Literal["local_workspace"] = "local_workspace"
    source: Literal["yahoo"] = "yahoo"


class LegacyDataSourceStatus(ContractModel):
    id: Literal["fred", "alpha_vantage", "typesafe_jev", "sec_edgar"]
    name: str
    environment_configured: bool
    integration: Literal["research_tool_only"] = "research_tool_only"
    purpose: str
    market_feed_enabled: Literal[False] = False
    connection_verified: Literal[False] = False


class MarketCacheStat(ContractModel):
    id: Literal["market_bars", "frozen_market_snapshots"]
    file_count: int = Field(ge=0)
    bytes: int = Field(ge=0)
    clearable: bool
    truncated: bool = False


class DataStatusResponse(ContractModel):
    received_at: datetime
    settings_revision: int
    activation: Literal["active", "saved_pending", "restart_required"]
    market_provider: MarketCapabilities | None
    market_provider_available: bool
    legacy_sources: list[LegacyDataSourceStatus]
    cache: list[MarketCacheStat]
    notices: list[str]


class DataCacheClearResponse(ContractModel):
    cleared_at: datetime
    removed_file_count: int = Field(ge=0)
    removed_bytes: int = Field(ge=0)
    failed_file_count: int = Field(ge=0)
    in_memory_invalidated: bool
    retained: list[
        Literal["frozen_market_snapshots", "research_artifacts", "credentials", "provider_session"]
    ]


class DataSettingsServiceProtocol(Protocol):
    def get_settings(self) -> DataSettingsResponse: ...
    def update_settings(self, body: DataSettingsUpdate) -> DataSettingsResponse: ...
    def apply_saved_settings(self) -> DataSettingsResponse: ...
    def get_status(self) -> DataStatusResponse: ...
    def clear_market_cache(self) -> DataCacheClearResponse: ...
