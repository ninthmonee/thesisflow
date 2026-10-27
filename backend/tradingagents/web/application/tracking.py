"""Bounded, owner-scoped watchlist observations over the existing numeric market service."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from tradingagents.web.api.tracking_schemas import (
    TrackingError,
    WatchlistTrackingItem,
    WatchlistTrackingResponse,
)
from tradingagents.web.domain.tracking import analyze_tracking, tracking_rules
from tradingagents.web.infrastructure.models import Instrument, Membership, Watchlist, WatchlistItem


class WatchlistTrackingService:
    def __init__(
        self,
        database,
        local_workspace,
        market_data,
        *,
        clock=None,
        max_workers=4,
        timeout_seconds=15,
        cache_seconds=120,
        refresh_min_seconds=30,
    ):
        if (
            not 1 <= max_workers <= 8
            or timeout_seconds <= 0
            or cache_seconds <= 0
            or refresh_min_seconds <= 0
        ):
            raise ValueError("Invalid tracking limits")
        self.database, self.market_data = database, market_data
        self.workspace_id, self.owner_id = local_workspace.workspace_id, local_workspace.user_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timeout_seconds, self.cache_seconds, self.refresh_min_seconds = (
            timeout_seconds,
            cache_seconds,
            refresh_min_seconds,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="watchlist-tracking"
        )
        self._lock, self._cache, self._pending = threading.RLock(), {}, {}
        self._generation, self._completion, self._closed = 0, 0, False

    def now(self):
        stamp = self.clock()
        if stamp.tzinfo is None:
            raise ValueError("Tracking clock must have timezone")
        return stamp.astimezone(UTC)

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self._cache.clear()
            # Retain old work in the bounded pending set; its generation cannot repopulate caches.
            # Repeated invalidations therefore cannot stack unbounded executor jobs.

    def _authorize(self, session):
        role = session.scalar(
            select(Membership.role).where(
                Membership.workspace_id == self.workspace_id, Membership.user_id == self.owner_id
            )
        )
        if role is None:
            raise TrackingError("tracking_forbidden", "当前用户无权读取此工作区的自选观察", 403)

    def rules(self):
        with self.database.session() as session:
            self._authorize(session)
        return tracking_rules()

    def _selection(self, watchlist_id, instrument_ids, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise TrackingError("invalid_tracking_limit", "每次读取数量须为1–100", 422)
        if instrument_ids is not None:
            if (
                not isinstance(instrument_ids, (list, tuple))
                or not 1 <= len(instrument_ids) <= 100
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > 64
                    or value != value.strip()
                    for value in instrument_ids
                )
            ):
                raise TrackingError("invalid_tracking_ids", "证券ID须为1–100个有效ID", 422)
            instrument_ids = list(dict.fromkeys(instrument_ids))
            try:
                if any(str(UUID(value)) != value.lower() for value in instrument_ids):
                    raise ValueError
            except ValueError:
                raise TrackingError("invalid_tracking_ids", "证券ID须为标准UUID格式", 422) from None
        with self.database.session() as session:
            self._authorize(session)
            if watchlist_id is not None:
                group = session.scalar(
                    select(Watchlist.id).where(
                        Watchlist.id == watchlist_id,
                        Watchlist.workspace_id == self.workspace_id,
                        Watchlist.owner_id == self.owner_id,
                    )
                )
                if group is None:
                    raise TrackingError("tracking_watchlist_not_found", "自选分组不存在", 404)
            query = (
                select(Instrument)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(
                    Watchlist.workspace_id == self.workspace_id,
                    Watchlist.owner_id == self.owner_id,
                    WatchlistItem.workspace_id == self.workspace_id,
                )
                .order_by(
                    Watchlist.position,
                    Watchlist.created_at,
                    Watchlist.id,
                    WatchlistItem.position,
                    WatchlistItem.created_at,
                    Instrument.id,
                )
            )
            if watchlist_id is not None:
                query = query.where(Watchlist.id == watchlist_id)
            unique = {}
            for instrument in session.scalars(query):
                unique.setdefault(
                    instrument.id,
                    {
                        field: getattr(instrument, field)
                        for field in ("id", "symbol", "name", "asset_type", "currency")
                    },
                )
            if instrument_ids is not None and not set(instrument_ids) <= set(unique):
                raise TrackingError(
                    "tracking_instrument_not_followed", "部分证券不在当前用户所选自选范围内", 404
                )
            selected = [
                item
                for key, item in unique.items()
                if instrument_ids is None or key in instrument_ids
            ]
            return selected[:limit], len(unique), len(selected)

    @staticmethod
    def _supported(instrument):
        symbol = instrument["symbol"]
        return (
            instrument["asset_type"] in {"stock", "etf"}
            and instrument["currency"] == "USD"
            and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}", symbol) is not None
            and ("." not in symbol or re.fullmatch(r"[A-Z0-9]+\.[AB]", symbol) is not None)
            and not symbol.endswith(
                ("-USD", "-USDT", "-USDC", "-EUR", "-GBP", "-JPY", "-CAD", "-AUD", "-BTC", "-ETH")
            )
        )

    def _fetch(self, symbol):
        try:
            result = self.market_data.get_bars(
                symbol,
                interval="1d",
                period="6mo",
                session="regular",
                adjustment="provider_native",
                limit=5000,
            )
            result = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            return (
                deepcopy(result)
                if isinstance(result, dict) and result.get("symbol") == symbol
                else {}
            )
        except Exception:
            return {}

    def _reap(self):
        completed = set()
        for identifier, (generation, future) in list(self._pending.items()):
            if not future.done():
                continue
            if generation == self._generation:
                self._completion += 1
                try:
                    self._cache[identifier] = (self.now(), future.result(), self._completion)
                except Exception:
                    self._cache[identifier] = (self.now(), {}, self._completion)
                completed.add(identifier)
            del self._pending[identifier]
        if len(self._cache) > 300:
            oldest = sorted(self._cache, key=lambda key: self._cache[key][0])
            for identifier in oldest[: len(self._cache) - 300]:
                del self._cache[identifier]
        return completed

    def _collect(self, instruments, refresh):
        with self._lock:
            if self._closed:
                raise TrackingError("tracking_closed", "自选观察服务已关闭", 503)
            completed, fresh, throttled = self._reap(), set(), set()
            tokens_before = {
                item["id"]: self._cache[item["id"]][2] if item["id"] in self._cache else None
                for item in instruments
            }
            now = self.now()
            for instrument in instruments:
                identifier = instrument["id"]
                cached = self._cache.get(identifier)
                age = (now - cached[0]).total_seconds() if cached else None
                source_ok = (
                    cached is not None
                    and (cached[1].get("meta") or {}).get("quality") == "complete"
                )
                ttl = self.cache_seconds if source_ok else min(30, self.cache_seconds)
                if age is not None and 0 <= age < (self.refresh_min_seconds if refresh else ttl):
                    fresh.add(identifier)
                    if refresh:
                        throttled.add(identifier)
                    continue
                if identifier not in self._pending and len(self._pending) < 100:
                    self._pending[identifier] = (
                        self._generation,
                        self._executor.submit(self._fetch, instrument["symbol"]),
                    )
            futures = [
                self._pending[item["id"]][1]
                for item in instruments
                if item["id"] not in fresh and item["id"] in self._pending
            ]
        if futures:
            wait(futures, timeout=self.timeout_seconds)
        with self._lock:
            completed |= self._reap()
            result = {}
            for instrument in instruments:
                identifier = instrument["id"]
                cached = self._cache.get(identifier)
                if cached:
                    state = (
                        "fresh"
                        if identifier in completed or cached[2] != tokens_before[identifier]
                        else "hit"
                        if identifier in fresh
                        else "stale"
                    )
                    result[identifier] = deepcopy(cached[1]), state, identifier in throttled
                else:
                    result[identifier] = {}, "fresh", False
            return result

    def get_tracking(self, watchlist_id=None, instrument_ids=None, refresh=False, limit=50):
        if not isinstance(refresh, bool):
            raise TrackingError("invalid_tracking_refresh", "刷新参数须为布尔值", 422)
        selected, total, requested = self._selection(watchlist_id, instrument_ids, limit)
        supported = [instrument for instrument in selected if self._supported(instrument)]
        collected = self._collect(supported, refresh) if supported else {}
        now, items, throttled = self.now(), [], False
        for instrument in selected:
            response, state, limited = collected.get(instrument["id"], ({}, "fresh", False))
            analysis = analyze_tracking(response, now)
            if not self._supported(instrument):
                analysis["rule_reasons"] = [
                    "仅支持美国上市股票/ETF的USD日线；此证券类型、币种或市场后缀不在本批覆盖内。"
                ]
            elif not response:
                analysis["rule_reasons"].append(
                    "来源正在读取、超时或未返回覆盖；未以模拟行情填补。"
                )
            analysis["meta"].cache = state
            if state == "stale":
                analysis["meta"].quality = "stale"
                analysis["meta"].warnings.append(
                    "刷新尚未完成或上游不可用，显示已标记过期的原始缓存；目标/保护参考已清空。"
                )
                analysis.update(
                    reference_target=None, protection_level=None, eligible_for_levels=False
                )
            throttled |= limited
            items.append(
                WatchlistTrackingItem(
                    instrument_id=instrument["id"],
                    symbol=instrument["symbol"],
                    name=instrument["name"],
                    asset_type=instrument["asset_type"],
                    currency=instrument["currency"],
                    **analysis,
                )
            )
        # Never serve a cached response without checking current membership.
        with self.database.session() as session:
            self._authorize(session)
        return WatchlistTrackingResponse(
            items=items,
            rules=tracking_rules(),
            received_at=now,
            total_count=total,
            requested_count=requested,
            returned_count=len(items),
            truncated=requested > len(items),
            refresh_throttled=throttled,
            watchlist_id=watchlist_id,
        )
