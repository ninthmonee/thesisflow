"""Persist bounded local market settings and manage only rebuildable market bars."""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, text

from tradingagents.web.api.data_schemas import (
    DataCacheClearResponse,
    DataRuntimeSettings,
    DataSettingsError,
    DataSettingsResponse,
    DataSettingsUpdate,
    DataStatusResponse,
    LegacyDataSourceStatus,
    MarketCacheStat,
)
from tradingagents.web.api.market_schemas import MarketCapabilities
from tradingagents.web.infrastructure.data_settings_model import DataSettingsRecord
from tradingagents.web.infrastructure.models import Membership

_CACHE_ENTRY_LIMIT = 10_000
_BAR_NAME = re.compile(r"[0-9a-f]{64}\.parquet")
_SNAPSHOT_NAME = re.compile(r"mkt-[0-9a-f]{32}\.json")


class DataSettingsService:
    def __init__(
        self,
        database,
        local_workspace,
        market_cache_root: str | Path,
        *,
        snapshot_cache_root: str | Path | None = None,
        apply_callback: Callable[[DataRuntimeSettings], bool | None] | None = None,
        cache_invalidator: Callable[[], bool | None] | None = None,
        capabilities_reader: Callable | None = None,
        environment_reader: Callable[[str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        # No filesystem, environment, provider, or DB access until an explicit method call.
        self.database = database
        self.workspace_id, self.owner_id = local_workspace.workspace_id, local_workspace.user_id
        self.market_cache_root = Path(market_cache_root).expanduser().absolute()
        self.snapshot_cache_root = (
            Path(snapshot_cache_root).expanduser().absolute()
            if snapshot_cache_root is not None
            else None
        )
        self.apply_callback, self.cache_invalidator = apply_callback, cache_invalidator
        self.capabilities_reader = capabilities_reader
        self.environment_reader = environment_reader or os.getenv
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._active_revision: int | None = None
        self._pending_revision: int | None = None

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("data settings clock must include timezone")
        return value.astimezone(UTC)

    def _authorize(self, session, *, write: bool = False) -> None:
        role = session.scalar(
            select(Membership.role).where(
                Membership.workspace_id == self.workspace_id,
                Membership.user_id == self.owner_id,
            )
        )
        if role is None or (write and role != "owner"):
            raise DataSettingsError(
                "data_settings_forbidden", "当前用户无权执行此数据中心操作", 403
            )

    def _response(self, row) -> DataSettingsResponse:
        revision = row.revision if row is not None else 0
        try:
            settings = DataRuntimeSettings.model_validate(row.configuration if row else {})
        except ValidationError:
            raise DataSettingsError(
                "invalid_saved_data_settings", "已保存的数据设置未通过校验，请重新保存有效配置", 409
            ) from None
        activation = (
            "active"
            if self._active_revision == revision
            else "saved_pending"
            if self._pending_revision == revision
            else "restart_required"
        )
        return DataSettingsResponse(
            revision=revision,
            settings=settings,
            updated_at=row.updated_at if row is not None else None,
            activation=activation,
        )

    def get_settings(self) -> DataSettingsResponse:
        with self._lock, self.database.session() as session:
            self._authorize(session)
            return self._response(session.get(DataSettingsRecord, self.workspace_id))

    def _activate(self, response: DataSettingsResponse) -> None:
        if self.apply_callback is None:
            return
        try:
            applied = self.apply_callback(response.settings.model_copy(deep=True))
        except Exception:
            # Persistence remains valid; do not expose paths/credentials from callback errors.
            self._pending_revision = None
            return
        if applied is False:
            self._pending_revision = response.revision
        else:
            self._active_revision, self._pending_revision = response.revision, None

    def apply_saved_settings(self) -> DataSettingsResponse:
        """Explicit startup/retry hook; constructors and GET never change runtime settings."""
        with self._lock:
            with self.database.session() as session:
                self._authorize(session, write=True)
            response = self.get_settings()
            self._activate(response)
            return self.get_settings()

    def update_settings(self, body: DataSettingsUpdate | dict) -> DataSettingsResponse:
        if not isinstance(body, DataSettingsUpdate):
            body = DataSettingsUpdate.model_validate(body)
        with self._lock:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._authorize(session, write=True)
                row = session.get(DataSettingsRecord, self.workspace_id)
                revision = row.revision if row is not None else 0
                if body.revision != revision:
                    raise DataSettingsError(
                        "data_settings_revision_conflict", "数据设置已被修改，请刷新后重试", 409
                    )
                now = self._now()
                if row is None:
                    row = DataSettingsRecord(
                        workspace_id=self.workspace_id,
                        configuration=body.settings.model_dump(),
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    row.configuration = body.settings.model_dump()
                    row.revision += 1
                    row.updated_at = now
                session.flush()
                saved = self._response(row)
            self._activate(saved)
            return self.get_settings()

    @staticmethod
    def _entries(root: Path | None, pattern: re.Pattern) -> Iterator[Path]:
        if root is None:
            return
        # No recursive walk; reject symlinked roots/ancestors and symlinked files.
        if any(parent.is_symlink() for parent in (root, *root.parents)) or not root.is_dir():
            return
        try:
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= _CACHE_ENTRY_LIMIT:
                        break
                    if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
        except OSError:
            return

    @staticmethod
    def _stat(
        root: Path | None, pattern: re.Pattern, cache_id, *, clearable: bool
    ) -> MarketCacheStat:
        count, size = 0, 0
        for path in DataSettingsService._entries(root, pattern):
            try:
                info = path.lstat()
                if path.is_symlink():
                    continue
            except OSError:
                continue
            count, size = count + 1, size + info.st_size
        # The bounded scan deliberately reports partial statistics for larger directories.
        truncated = False
        if root is not None and not any(parent.is_symlink() for parent in (root, *root.parents)):
            try:
                with os.scandir(root) as entries:
                    truncated = (
                        sum(1 for _entry in islice(entries, _CACHE_ENTRY_LIMIT + 1))
                        > _CACHE_ENTRY_LIMIT
                    )
            except OSError:
                pass
        return MarketCacheStat(
            id=cache_id, file_count=count, bytes=size, clearable=clearable, truncated=truncated
        )

    def get_status(self) -> DataStatusResponse:
        settings = self.get_settings()
        provider = None
        if self.capabilities_reader is not None:
            with suppress(Exception):
                provider = MarketCapabilities.model_validate(self.capabilities_reader())
        definitions = (
            ("fred", "FRED", "FRED_API_KEY", "研究工具中的宏观经济序列；未接入此行情工作台"),
            (
                "alpha_vantage",
                "Alpha Vantage",
                "ALPHA_VANTAGE_API_KEY",
                "研究工具的历史行情、指标与财务数据来源；未接入此行情工作台",
            ),
            (
                "typesafe_jev",
                "TypeSafe Jev",
                "TYPESAFE_API_KEY",
                "研究流程中的社交帖子相关性与立场筛选；不是实时行情源",
            ),
            (
                "sec_edgar",
                "SEC EDGAR",
                "SEC_EDGAR_USER_AGENT",
                "研究工具的美股公司公告与申报财务；环境字段为联系身份，不是 API Key",
            ),
        )
        legacy = []
        for source_id, name, environment_key, purpose in definitions:
            try:
                value = self.environment_reader(environment_key)
                configured = isinstance(value, str) and bool(value.strip())
            except Exception:
                configured = False
            legacy.append(
                LegacyDataSourceStatus(
                    id=source_id,
                    name=name,
                    environment_configured=configured,
                    purpose=purpose,
                )
            )
        return DataStatusResponse(
            received_at=self._now(),
            settings_revision=settings.revision,
            activation=settings.activation,
            market_provider=provider,
            market_provider_available=provider is not None,
            legacy_sources=legacy,
            cache=[
                self._stat(
                    self.market_cache_root / "bars", _BAR_NAME, "market_bars", clearable=True
                ),
                self._stat(
                    self.snapshot_cache_root,
                    _SNAPSHOT_NAME,
                    "frozen_market_snapshots",
                    clearable=False,
                ),
            ],
            notices=[
                "行情当前仅接入 Yahoo；能力声明不代表连接已通过网络验证。",
                "价格是来源 K 线收盘字段，不是逐笔、NBBO 或保证实时的报价。",
                "研究工具的环境存在性不代表凭据有效、调用成功或已接入此行情工作台。",
                "清理仅删除应用生成的行情柱缓存；冻结快照、研究文件、凭据与供应商会话均保留。",
            ],
        )

    def clear_market_cache(self) -> DataCacheClearResponse:
        with self._lock:
            with self.database.session() as session:
                self._authorize(session, write=True)
            removed, size, failed = 0, 0, 0
            for path in self._entries(self.market_cache_root / "bars", _BAR_NAME):
                try:
                    info = path.lstat()
                    if path.is_symlink():
                        continue
                    path.unlink()
                    removed, size = removed + 1, size + info.st_size
                except OSError:
                    failed += 1
            invalidated = False
            if self.cache_invalidator is not None:
                with suppress(Exception):
                    invalidated = self.cache_invalidator() is not False
            return DataCacheClearResponse(
                cleared_at=self._now(),
                removed_file_count=removed,
                removed_bytes=size,
                failed_file_count=failed,
                in_memory_invalidated=invalidated,
                retained=[
                    "frozen_market_snapshots",
                    "research_artifacts",
                    "credentials",
                    "provider_session",
                ],
            )
