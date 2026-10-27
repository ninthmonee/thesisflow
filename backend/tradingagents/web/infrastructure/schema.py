"""Register all revisions' entities for Alembic metadata comparison, without I/O."""

from .alternative_models import AlternativeDataConnection
from .auth_models import (
    AccountRecord,
    AccountSessionRecord,
    AuthenticationRateLimitRecord,
    AuthenticationSettingsRecord,
)
from .data_settings_model import DataSettingsRecord
from .models import Base
from .research_data_model import ResearchDataConnectionRecord
from .theme_model import ThemeMemberRecord, ThemeRecord, ThemeVersionRecord
from .viewpoint_model import ResearchViewpoint
from .watchlist_observation_model import WatchlistObservationRecord
from .worker_health_model import WorkerHeartbeatRecord

__all__ = [
    "Base",
    "AccountRecord",
    "AccountSessionRecord",
    "AuthenticationRateLimitRecord",
    "AuthenticationSettingsRecord",
    "DataSettingsRecord",
    "AlternativeDataConnection",
    "ResearchDataConnectionRecord",
    "ThemeMemberRecord",
    "ThemeRecord",
    "ThemeVersionRecord",
    "WatchlistObservationRecord",
    "WorkerHeartbeatRecord",
    "ResearchViewpoint",
]
