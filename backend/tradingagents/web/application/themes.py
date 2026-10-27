"""Workspace-scoped editable observation baskets with immutable version membership."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from tradingagents.web.api.themes_schemas import (
    ThemeAnalytics,
    ThemeClone,
    ThemeError,
    ThemeMemberAdd,
    ThemeMemberInput,
    ThemeMemberMetric,
    ThemeMemberView,
    ThemePin,
    ThemeUpdate,
    ThemeView,
    ThemeWrite,
    member_evidence_quality,
)
from tradingagents.web.domain.theme_catalog import (
    CONSOLIDATED_MEMORY_THEME,
    LEGACY_MEMORY_CATALOG,
    THEME_CATALOG,
)
from tradingagents.web.infrastructure.models import Instrument, Membership
from tradingagents.web.infrastructure.theme_model import (
    ThemeMemberRecord,
    ThemeRecord,
    ThemeVersionRecord,
)


def finite(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and (not positive or value > 0) else None


class ThemesService:
    def __init__(
        self,
        database,
        local_workspace,
        market_data,
        *,
        clock=None,
        max_workers=4,
        timeout_seconds=20,
        cache_seconds=180,
    ):
        if not 1 <= max_workers <= 8 or timeout_seconds <= 0 or cache_seconds <= 0:
            raise ValueError("Invalid theme aggregation limits")
        self.database, self.market_data = database, market_data
        self.workspace_id, self.owner_id = local_workspace.workspace_id, local_workspace.user_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timeout_seconds, self.cache_seconds = timeout_seconds, cache_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="theme-data"
        )
        self._lock, self._build_lock = threading.RLock(), threading.Lock()
        self._data_cache, self._analytics_cache, self._pending = {}, {}, {}

    def now(self):
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("Theme clock must include timezone")
        return value.astimezone(UTC)

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def invalidate(self):
        with self._lock:
            self._data_cache.clear()
            self._analytics_cache.clear()

    def _authorize(self, session, write=False):
        role = session.scalar(
            select(Membership.role).where(
                Membership.workspace_id == self.workspace_id, Membership.user_id == self.owner_id
            )
        )
        if role is None:
            raise ThemeError("workspace_not_found", "工作区不存在", 404)
        if write and role not in {"owner", "researcher"}:
            raise ThemeError("theme_forbidden", "当前权限不能修改主题", 403)

    def _record(self, session, theme_id):
        record = session.scalar(
            select(ThemeRecord).where(
                ThemeRecord.id == theme_id,
                ThemeRecord.workspace_id == self.workspace_id,
                ThemeRecord.owner_id == self.owner_id,
            )
        )
        if record is None:
            raise ThemeError("theme_not_found", "主题不存在", 404)
        return record

    @staticmethod
    def _revision(record, revision):
        if record.revision != revision:
            raise ThemeError("theme_revision_conflict", "主题已被修改，请刷新后重试", 409)

    def _version(self, session, record, version=None):
        result = session.scalar(
            select(ThemeVersionRecord).where(
                ThemeVersionRecord.theme_id == record.id,
                ThemeVersionRecord.version == (version or record.current_version),
            )
        )
        if result is None:
            raise ThemeError("theme_version_not_found", "主题版本不存在", 404)
        return result

    def _view(self, session, record, version=None):
        current = version is None
        version = self._version(session, record, version)
        members = session.scalars(
            select(ThemeMemberRecord)
            .where(ThemeMemberRecord.version_id == version.id)
            .order_by(ThemeMemberRecord.position)
        ).all()
        configuration = deepcopy(version.configuration)
        archived = configuration.pop("archived", False)
        version_revision = configuration.pop("_record_revision", version.version)
        return ThemeView(
            **configuration,
            members=[
                ThemeMemberView(
                    **deepcopy(item.content),
                    evidence_quality=member_evidence_quality(
                        ThemeMemberInput.model_validate(item.content)
                    ),
                )
                for item in members
            ],
            id=record.id,
            revision=record.revision if current else version_revision,
            version=version.version,
            is_builtin=record.builtin_key is not None,
            archived=archived,
            pinned=record.pinned,
            effective_from=version.effective_from,
            created_at=record.created_at,
            updated_at=record.updated_at if current else version.created_at,
        )

    @staticmethod
    def _configuration(view):
        """Read-only relationship completeness is never persisted as user input."""
        result = view.model_dump(include=set(ThemeWrite.model_fields))
        result["members"] = [
            member.model_dump(include=set(ThemeMemberInput.model_fields)) for member in view.members
        ]
        return result

    def _save_version(self, session, record, body, *, archived=False):
        configuration = body.model_dump(exclude={"members"})
        configuration["archived"] = archived
        configuration["_record_revision"] = record.revision
        version = ThemeVersionRecord(
            id=str(uuid4()),
            theme_id=record.id,
            version=record.current_version,
            configuration=configuration,
            effective_from=self.now(),
            created_at=self.now(),
        )
        session.add(version)
        session.flush()
        for position, member in enumerate(body.members):
            instrument_id = session.scalar(
                select(Instrument.id)
                .where(Instrument.symbol == member.symbol)
                .order_by(Instrument.created_at)
                .limit(1)
            )
            session.add(
                ThemeMemberRecord(
                    id=str(uuid4()),
                    version_id=version.id,
                    instrument_id=instrument_id,
                    symbol=member.symbol,
                    position=position,
                    content=member.model_dump(),
                )
            )
        session.flush()

    def _new(self, session, body, builtin_key=None):
        if builtin_key is None:
            count = session.scalar(
                select(func.count())
                .select_from(ThemeRecord)
                .where(
                    ThemeRecord.workspace_id == self.workspace_id,
                    ThemeRecord.owner_id == self.owner_id,
                    ThemeRecord.builtin_key.is_(None),
                )
            )
            if count >= 200:
                raise ThemeError("theme_limit", "自定义主题已达到200个上限（含已删除主题）", 409)
        record = ThemeRecord(
            id=str(uuid4()),
            workspace_id=self.workspace_id,
            owner_id=self.owner_id,
            builtin_key=builtin_key,
            current_version=1,
            archived=False,
            revision=1,
            created_at=self.now(),
            updated_at=self.now(),
        )
        session.add(record)
        session.flush()
        self._save_version(session, record, body)
        return record

    def ensure_defaults(self):
        """Explicit startup seed; existing/edited/archived builtins are never overwritten."""
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            existing = set(
                session.scalars(
                    select(ThemeRecord.builtin_key).where(
                        ThemeRecord.workspace_id == self.workspace_id,
                        ThemeRecord.owner_id == self.owner_id,
                    )
                )
            )
            for entry in THEME_CATALOG:
                if entry["key"] not in existing:
                    self._new(
                        session,
                        ThemeWrite.model_validate(
                            {key: deepcopy(value) for key, value in entry.items() if key != "key"}
                        ),
                        entry["key"],
                    )
            self._upgrade_untouched_memory_defaults(session)

    def _upgrade_untouched_memory_defaults(self, session):
        """Consolidate only the exact original pair, retaining IDs and all history."""
        rows = {
            row.builtin_key: row
            for row in session.scalars(
                select(ThemeRecord).where(
                    ThemeRecord.workspace_id == self.workspace_id,
                    ThemeRecord.owner_id == self.owner_id,
                    ThemeRecord.builtin_key.in_(entry["key"] for entry in LEGACY_MEMORY_CATALOG),
                )
            )
        }
        snapshots = {}
        for entry in LEGACY_MEMORY_CATALOG:
            record = rows.get(entry["key"])
            if record is None or record.archived or record.current_version != 1:
                return
            current = self._view(session, record)
            expected = ThemeWrite.model_validate(
                {key: value for key, value in entry.items() if key != "key"}
            )
            if self._configuration(current) != expected.model_dump():
                return
            snapshots[entry["key"]] = current
        storage, hbm = rows["memory-storage"], rows["hbm-dram"]
        if hbm.pinned and (
            not storage.pinned
            or storage.pinned_at is None
            or (hbm.pinned_at is not None and hbm.pinned_at > storage.pinned_at)
        ):
            storage.pinned, storage.pinned_at = True, hbm.pinned_at or self.now()
        configurations = (
            (
                storage,
                ThemeWrite.model_validate(
                    {key: value for key, value in CONSOLIDATED_MEMORY_THEME.items() if key != "key"}
                ),
                False,
            ),
            (
                hbm,
                ThemeWrite.model_validate(self._configuration(snapshots["hbm-dram"])),
                True,
            ),
        )
        for record, body, archived in configurations:
            record.revision += 1
            record.current_version += 1
            record.archived, record.updated_at = archived, self.now()
            self._save_version(session, record, body, archived=archived)

    def list_themes(self, *, include_archived=False):
        with self.database.session() as session:
            self._authorize(session)
            query = select(ThemeRecord).where(
                ThemeRecord.workspace_id == self.workspace_id, ThemeRecord.owner_id == self.owner_id
            )
            if not include_archived:
                query = query.where(ThemeRecord.archived.is_(False))
            rows = session.scalars(
                query.order_by(
                    ThemeRecord.pinned.desc(),
                    ThemeRecord.pinned_at.desc(),
                    ThemeRecord.created_at,
                    ThemeRecord.id,
                )
            ).all()
            return {"items": [self._view(session, row) for row in rows]}

    def get_theme(self, theme_id, *, version=None):
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1
        ):
            raise ThemeError("invalid_theme_version", "主题版本须为正整数", 422)
        with self.database.session() as session:
            self._authorize(session)
            return self._view(session, self._record(session, theme_id), version)

    def list_versions(self, theme_id):
        with self.database.session() as session:
            self._authorize(session)
            record = self._record(session, theme_id)
            versions = session.scalars(
                select(ThemeVersionRecord.version)
                .where(ThemeVersionRecord.theme_id == record.id)
                .order_by(ThemeVersionRecord.version.desc())
            ).all()
            return {"items": [self._view(session, record, version) for version in versions]}

    def create_theme(self, body):
        body = ThemeWrite.model_validate(body.model_dump() if hasattr(body, "model_dump") else body)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            result = self._view(session, self._new(session, body))
        return result

    def _mutate(self, theme_id, revision, change, *, restore=False):
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            record = self._record(session, theme_id)
            self._revision(record, revision)
            if record.archived and not restore:
                raise ThemeError("theme_archived", "主题已删除，可恢复或复制为新主题", 409)
            if restore and not record.archived:
                return self._view(session, record)
            old = self._view(session, record)
            body, archive = change(old)
            body = ThemeWrite.model_validate(body)
            record.revision += 1
            record.current_version += 1
            record.archived, record.updated_at = archive, self.now()
            self._save_version(session, record, body, archived=archive)
            result = self._view(session, record)
        return result

    def update_theme(self, theme_id, body):
        body = ThemeUpdate.model_validate(
            body.model_dump() if hasattr(body, "model_dump") else body
        )

        def change(old):
            new = ThemeWrite.model_validate(body.model_dump(exclude={"revision"}))
            return new.model_dump(), False

        return self._mutate(theme_id, body.revision, change)

    def archive_theme(self, theme_id, revision):
        return self._mutate(
            theme_id,
            revision,
            lambda old: (self._configuration(old), True),
        )

    def restore_theme(self, theme_id, revision):
        return self._mutate(
            theme_id,
            revision,
            lambda old: (self._configuration(old), False),
            restore=True,
        )

    def set_theme_pinned(self, theme_id, body):
        body = ThemePin.model_validate(body.model_dump() if hasattr(body, "model_dump") else body)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            record = self._record(session, theme_id)
            self._revision(record, body.revision)
            if record.pinned != body.pinned:
                record.pinned = body.pinned
                record.pinned_at = self.now() if body.pinned else None
                record.revision += 1
                record.updated_at = self.now()
                session.flush()
            return self._view(session, record)

    def clone_theme(self, theme_id, body):
        body = ThemeClone.model_validate(body.model_dump() if hasattr(body, "model_dump") else body)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            record = self._record(session, theme_id)
            self._revision(record, body.revision)
            old = self._view(session, record)
            configuration = self._configuration(old)
            configuration["name"] = body.name
            return self._view(session, self._new(session, ThemeWrite.model_validate(configuration)))

    def add_member(self, theme_id, body):
        body = ThemeMemberAdd.model_validate(
            body.model_dump() if hasattr(body, "model_dump") else body
        )

        def change(old):
            if len(old.members) >= 100:
                raise ThemeError("theme_member_limit", "单一主题最多100个成员", 409)
            if any(item.symbol == body.member.symbol for item in old.members):
                raise ThemeError("theme_member_exists", "此证券已在主题中", 409)
            configuration = self._configuration(old)
            configuration["members"].append(body.member.model_dump())
            return configuration, False

        return self._mutate(theme_id, body.revision, change)

    def remove_member(self, theme_id, symbol, revision):
        symbol = symbol.strip().upper()

        def change(old):
            if not any(item.symbol == symbol for item in old.members):
                raise ThemeError("theme_member_not_found", "主题中没有此证券", 404)
            configuration = self._configuration(old)
            configuration["members"] = [
                item for item in configuration["members"] if item["symbol"] != symbol
            ]
            return configuration, False

        return self._mutate(theme_id, revision, change)

    def _load(self, symbol):
        try:
            response = self.market_data.get_bars(
                symbol,
                interval="1d",
                period="3mo",
                session="regular",
                adjustment="provider_native",
                limit=5000,
            )
            response = (
                response.model_dump(mode="json") if hasattr(response, "model_dump") else response
            )
            return (
                dict(response)
                if isinstance(response, Mapping) and response.get("symbol") == symbol
                else {}
            )
        except Exception:
            return {}

    def _collect(self, symbols, refresh):
        now = self.now()
        with self._lock:
            for symbol, future in list(self._pending.items()):
                if future.done():
                    try:
                        self._data_cache[symbol] = (now, future.result())
                    except Exception:
                        self._data_cache[symbol] = (now, {})
                    del self._pending[symbol]
            needed = [
                symbol
                for symbol in symbols
                if symbol not in self._pending
                and (
                    symbol not in self._data_cache
                    or (now - self._data_cache[symbol][0]).total_seconds()
                    >= (
                        30
                        if refresh
                        or not self._data_cache[symbol][1]
                        or (self._data_cache[symbol][1].get("meta") or {}).get("quality")
                        != "complete"
                        else self.cache_seconds
                    )
                )
            ]
            for symbol in needed:
                # A theme is bounded to 100 members, and only one build owns this section.
                if len(self._pending) < 110:
                    self._pending[symbol] = self._executor.submit(self._load, symbol)
            futures = [self._pending[symbol] for symbol in symbols if symbol in self._pending]
        if futures:
            wait(futures, timeout=self.timeout_seconds)
        with self._lock:
            for symbol, future in list(self._pending.items()):
                if future.done():
                    try:
                        self._data_cache[symbol] = (self.now(), future.result())
                    except Exception:
                        self._data_cache[symbol] = (self.now(), {})
                    del self._pending[symbol]
                elif future.cancel():
                    del self._pending[symbol]
            if len(self._data_cache) > 600:
                oldest = sorted(self._data_cache, key=lambda key: self._data_cache[key][0])
                for key in oldest[: len(self._data_cache) - 600]:
                    self._data_cache.pop(key, None)
            return {
                symbol: deepcopy(self._data_cache.get(symbol, (None, {}))[1]) for symbol in symbols
            }

    @staticmethod
    def _rows(response, source=None, feed=None):
        meta = response.get("meta", {})
        if (
            not isinstance(meta, Mapping)
            or meta.get("quality") == "unavailable"
            or response.get("interval") != "1d"
            or response.get("session") != "regular"
            or response.get("adjustment") != "provider_native"
        ):
            return {}, "来源没有可用的同口径日线"
        if not meta.get("source") or not meta.get("feed"):
            return {}, "缺少来源/feed"
        if source is not None and (source != meta.get("source") or feed != meta.get("feed")):
            return {}, "来源/feed与SPY不同，已排除比较"
        rows = {
            row["date"]: row
            for row in response.get("bars", [])
            if isinstance(row, Mapping)
            and isinstance(row.get("date"), str)
            and row.get("is_complete") is True
            and finite(row.get("close"), positive=True) is not None
        }
        return rows, None if rows else "没有完整交易日收盘数据"

    def _build(self, theme, responses, window, *, original_member_count, documented_only):
        from exchange_calendars import get_calendar

        calendar = get_calendar("XNYS")
        spy = responses.get("SPY", {})
        spy_rows, _ = self._rows(spy)
        spy_meta = spy.get("meta", {})
        source, feed = spy_meta.get("source"), spy_meta.get("feed")
        end = max(spy_rows, default=None)
        start, expected, baseline = None, [], None
        if end:
            sessions = [
                label.strftime("%Y-%m-%d")
                for label in calendar.sessions_in_range(
                    datetime.fromisoformat(end).date() - timedelta(days=65), end
                )
            ]
            expected = sessions[-21:]
            if window in {"1D", "5D"}:
                distance = 1 if window == "1D" else 5
                start = sessions[-distance - 1] if len(sessions) > distance else None
            else:
                import calendar as month_calendar

                ending = datetime.fromisoformat(end).date()
                month, year = (
                    (ending.month - 1, ending.year) if ending.month > 1 else (12, ending.year - 1)
                )
                threshold = ending.replace(
                    year=year,
                    month=month,
                    day=min(ending.day, month_calendar.monthrange(year, month)[1]),
                ).isoformat()
                candidates = [stamp for stamp in sessions if stamp <= threshold]
                start = candidates[-1] if candidates else None
            if start in spy_rows:
                baseline = (spy_rows[end]["close"] / spy_rows[start]["close"] - 1) * 100
        metrics = []
        for member in theme.members:
            response = responses.get(member.symbol, {})
            meta = response.get("meta", {})
            rows, reason = self._rows(response, source, feed)
            if (
                member.symbol.startswith("^")
                and (meta.get("coverage") or {}).get("calendar_verified") is False
            ):
                today = self.now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
                rows = {stamp: row for stamp, row in rows.items() if stamp < today}
            value, rvol, turnover, reference_count = None, None, None, 0
            if baseline is not None and start in rows and end in rows and source and feed:
                value = (rows[end]["close"] / rows[start]["close"] - 1) * 100
                prior = [
                    finite(rows[stamp].get("volume")) for stamp in expected[:-1] if stamp in rows
                ]
                prior = [volume for volume in prior if volume is not None and volume >= 0]
                reference_count = len(prior)
                volume = finite(rows[end].get("volume"))
                if volume is not None and volume >= 0:
                    turnover = rows[end]["close"] * volume
                    if len(prior) == 20 and mean(prior) > 0:
                        rvol = volume / mean(prior)
            else:
                reason = reason or "缺少SPY共同起止日数据，未替代日期"
            quality = (
                "unavailable"
                if value is None
                else "stale"
                if meta.get("quality") == "stale"
                else "partial"
                if meta.get("quality") != "complete" or rvol is None
                else "complete"
            )
            metrics.append(
                ThemeMemberMetric(
                    **member.model_dump(),
                    return_percent=value,
                    relative_return_pp=value - baseline if value is not None else None,
                    daily_rvol=rvol,
                    rvol_sample_count=reference_count,
                    turnover_estimate_usd=turnover,
                    as_of=end if value is not None else None,
                    source=meta.get("source"),
                    feed=meta.get("feed"),
                    quality=quality,
                    reason=reason,
                )
            )
        valid = [row for row in metrics if row.return_percent is not None]
        returns = [row.return_percent for row in valid]
        rvols = [row.daily_rvol for row in valid if row.daily_rvol is not None]
        aggregate = mean(returns) if valid else None
        warnings = [
            "主题为代表性观察样本，可重叠；按所选版本成分计算最近完整交易日窗口，不是历史回测。",
            theme.evidence_notice,
        ]
        if documented_only:
            warnings.append(
                "仅纳入维护者已补齐关系类型、核查日期及来源链接的成员；不表示平台验证。"
            )
        if len(valid) < 3:
            warnings.append(
                "有效样本少于3只，仅代表小型观察篮子；单一成员时等同该股票，不代表全行业广度或排名。"
            )
        if len(valid) < len(metrics):
            warnings.append(f"仅{len(valid)}/{len(metrics)}个成员具有共同窗口数据，缺失未补零。")
        if any(row.daily_rvol is None for row in metrics):
            warnings.append("部分成员不足20个有效完整交易日成交量，日RVOL保留缺失。")
        quality = (
            "unavailable"
            if not valid
            else "stale"
            if spy_meta.get("quality") == "stale" or any(row.quality == "stale" for row in valid)
            else "partial"
            if len(valid) != len(metrics)
            or len(valid) < 3
            or spy_meta.get("quality") != "complete"
            or any(row.quality != "complete" for row in valid)
            else "complete"
        )
        return ThemeAnalytics(
            theme_id=theme.id,
            theme_name=theme.name,
            version=theme.version,
            window=window,
            window_start=start,
            window_end=end,
            received_at=self.now(),
            source=source,
            feed=feed,
            membership_effective_from=theme.effective_from,
            documented_only=documented_only,
            original_member_count=original_member_count,
            excluded_count=original_member_count - len(metrics),
            requested_count=len(metrics),
            available_count=len(valid),
            return_percent=aggregate,
            relative_return_pp=aggregate - baseline if aggregate is not None else None,
            spy_return_percent=baseline,
            advancers=sum(value > 0.000001 for value in returns) if valid else None,
            decliners=sum(value < -0.000001 for value in returns) if valid else None,
            unchanged=sum(abs(value) <= 0.000001 for value in returns) if valid else None,
            median_daily_rvol=median(rvols) if rvols else None,
            quality=quality,
            members=metrics,
            warnings=warnings,
        )

    def get_analytics(
        self, theme_id, *, window="1D", refresh=False, version=None, documented_only=False
    ):
        if window not in {"1D", "5D", "1M"}:
            raise ThemeError("invalid_theme_window", "主题窗口须为1D、5D或1M", 422)
        theme = self.get_theme(theme_id, version=version)
        if theme.archived:
            raise ThemeError("theme_archived", "主题已停用，可复制为新主题", 409)
        original_member_count = len(theme.members)
        if documented_only:
            theme = theme.model_copy(
                update={
                    "members": [
                        member
                        for member in theme.members
                        if member.evidence_quality == "documented"
                    ]
                }
            )
        build_options = {
            "original_member_count": original_member_count,
            "documented_only": documented_only,
        }
        if not theme.members:
            return self._build(theme, {}, window, **build_options)
        key = (theme.id, theme.version, window, documented_only)
        with self._lock:
            cached = self._analytics_cache.get(key)
            if (
                cached
                and not refresh
                and (self.now() - cached.received_at).total_seconds()
                < (30 if cached.quality != "complete" else self.cache_seconds)
            ):
                return cached.model_copy(deep=True)
        if not self._build_lock.acquire(blocking=False):
            if cached:
                return cached.model_copy(
                    update={
                        "quality": "stale",
                        "warnings": [*cached.warnings, "另一主题快照正在刷新，当前显示此前快照。"],
                    },
                    deep=True,
                )
            raise ThemeError("theme_analytics_building", "主题快照正在读取，请稍后重试", 503)
        try:
            symbols = list(dict.fromkeys(["SPY", *[member.symbol for member in theme.members]]))
            result = self._build(theme, self._collect(symbols, refresh), window, **build_options)
            with self._lock:
                self._analytics_cache[key] = result.model_copy(deep=True)
                if len(self._analytics_cache) > 100:
                    oldest = min(
                        self._analytics_cache,
                        key=lambda item: self._analytics_cache[item].received_at,
                    )
                    self._analytics_cache.pop(oldest)
            return result
        finally:
            self._build_lock.release()
