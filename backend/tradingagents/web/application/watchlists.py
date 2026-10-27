"""Private watchlists and a public instrument catalog with explicit source status."""

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError

from tradingagents.dataflows.vendors.web_market import YahooSearchProvider
from tradingagents.web.api.watchlists import WatchlistObservationPatch
from tradingagents.web.infrastructure.database import Database
from tradingagents.web.infrastructure.models import Instrument, Membership, Watchlist, WatchlistItem
from tradingagents.web.infrastructure.watchlist_observation_model import WatchlistObservationRecord


class WatchlistError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


yahoo_instrument_search = YahooSearchProvider()


def instrument_dict(instrument: Instrument) -> dict[str, Any]:
    return {
        key: getattr(instrument, key)
        for key in (
            "id",
            "symbol",
            "name",
            "asset_type",
            "exchange",
            "currency",
            "timezone",
        )
    }


class WatchlistService:
    def __init__(
        self,
        database: Database,
        *,
        workspace_id: str,
        owner_id: str,
        search_provider: Callable[[str], list[dict[str, Any]]] | None = None,
    ):
        self.database, self.workspace_id, self.owner_id = database, workspace_id, owner_id
        self.search_provider = search_provider or yahoo_instrument_search

    @contextmanager
    def _write(self):
        # Serialize short SQLite writes before reading a revision or group count.
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session

    @staticmethod
    def _name(value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise WatchlistError("invalid_name", "分组名称须为 1–120 个字符")
        return value

    def _owned(self, session, list_id: str) -> Watchlist:
        group = session.scalar(
            select(Watchlist).where(
                Watchlist.id == list_id,
                Watchlist.workspace_id == self.workspace_id,
                Watchlist.owner_id == self.owner_id,
            )
        )
        if group is None:
            raise WatchlistError("not_found", "自选分组不存在", 404)
        return group

    @staticmethod
    def _version(group: Watchlist, revision: int | None) -> None:
        if revision is not None and group.revision != revision:
            raise WatchlistError("revision_conflict", "列表已更新，请刷新后重试", 409)

    @staticmethod
    def _render(session, group: Watchlist) -> dict[str, Any]:
        rows = session.execute(
            select(WatchlistItem, Instrument, WatchlistObservationRecord)
            .join(
                Instrument,
                WatchlistItem.instrument_id == Instrument.id,
            )
            .outerjoin(
                WatchlistObservationRecord,
                and_(
                    WatchlistObservationRecord.instrument_id == Instrument.id,
                    WatchlistObservationRecord.workspace_id == group.workspace_id,
                    WatchlistObservationRecord.owner_id == group.owner_id,
                ),
            )
            .where(WatchlistItem.watchlist_id == group.id)
            .order_by(
                WatchlistItem.position,
                WatchlistItem.created_at,
            )
        ).all()
        return {
            "id": group.id,
            "name": group.name,
            "position": group.position,
            "revision": group.revision,
            "items": [
                {
                    "id": item.id,
                    "position": item.position,
                    "instrument": instrument_dict(instrument),
                    "favorite": observation.favorite if observation else False,
                    "notes": observation.notes if observation else "",
                    "observation_revision": observation.revision if observation else 0,
                }
                for item, instrument, observation in rows
            ],
        }

    def list_watchlists(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            groups = session.scalars(
                select(Watchlist)
                .where(
                    Watchlist.workspace_id == self.workspace_id,
                    Watchlist.owner_id == self.owner_id,
                )
                .order_by(Watchlist.position, Watchlist.created_at)
            ).all()
            return [self._render(session, group) for group in groups]

    def update_observation(self, instrument_id: str, body) -> dict[str, Any]:
        body = WatchlistObservationPatch.model_validate(
            body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body
        )
        changes = body.model_dump(include=body.model_fields_set - {"revision"})
        with self._write() as session:
            role = session.scalar(
                select(Membership.role).where(
                    Membership.workspace_id == self.workspace_id,
                    Membership.user_id == self.owner_id,
                )
            )
            if role is None:
                raise WatchlistError("workspace_not_found", "工作区不存在", 404)
            if role not in {"owner", "researcher"}:
                raise WatchlistError("observation_forbidden", "当前权限不能修改自选观察记录", 403)
            followed = session.scalar(
                select(WatchlistItem.id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(
                    Watchlist.workspace_id == self.workspace_id,
                    Watchlist.owner_id == self.owner_id,
                    WatchlistItem.workspace_id == self.workspace_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
                .limit(1)
            )
            if followed is None:
                raise WatchlistError("instrument_not_watched", "请先将证券加入当前工作区自选", 404)
            record = session.get(
                WatchlistObservationRecord,
                (self.workspace_id, self.owner_id, instrument_id),
            )
            if body.revision != (record.revision if record else 0):
                raise WatchlistError(
                    "observation_revision_conflict", "观察记录已更新，请刷新后重试", 409
                )
            if record is None:
                record = WatchlistObservationRecord(
                    workspace_id=self.workspace_id,
                    owner_id=self.owner_id,
                    instrument_id=instrument_id,
                    favorite=changes.get("favorite", False),
                    notes=changes.get("notes", ""),
                    revision=1,
                )
                session.add(record)
            else:
                for name, value in changes.items():
                    setattr(record, name, value)
                record.revision += 1
            session.flush()
            return {
                "instrument_id": record.instrument_id,
                "favorite": record.favorite,
                "notes": record.notes,
                "revision": record.revision,
                "updated_at": record.updated_at,
            }

    def create_watchlist(self, name: str) -> dict[str, Any]:
        try:
            with self._write() as session:
                position = session.scalar(
                    select(func.max(Watchlist.position)).where(
                        Watchlist.workspace_id == self.workspace_id,
                        Watchlist.owner_id == self.owner_id,
                    )
                )
                group = Watchlist(
                    workspace_id=self.workspace_id,
                    owner_id=self.owner_id,
                    name=self._name(name),
                    position=(position or 0) + 1,
                )
                session.add(group)
                session.flush()
                return self._render(session, group)
        except IntegrityError as exc:
            raise WatchlistError("duplicate_name", "已存在同名分组", 409) from exc

    def rename_watchlist(self, list_id: str, name: str, revision: int) -> dict[str, Any]:
        try:
            with self._write() as session:
                group = self._owned(session, list_id)
                self._version(group, revision)
                group.name = self._name(name)
                group.revision += 1
                session.flush()
                return self._render(session, group)
        except IntegrityError as exc:
            raise WatchlistError("duplicate_name", "已存在同名分组", 409) from exc

    def delete_watchlist(self, list_id: str, revision: int) -> None:
        with self._write() as session:
            group = self._owned(session, list_id)
            self._version(group, revision)
            count = session.scalar(
                select(func.count())
                .select_from(Watchlist)
                .where(
                    Watchlist.workspace_id == self.workspace_id,
                    Watchlist.owner_id == self.owner_id,
                )
            )
            if count == 1:
                raise WatchlistError("last_watchlist", "至少保留一个自选分组", 409)
            session.execute(delete(WatchlistItem).where(WatchlistItem.watchlist_id == list_id))
            session.delete(group)

    def search_instruments(self, query: str) -> dict[str, Any]:
        query = query.strip()
        warning, source = None, "local_catalog"
        if query:
            try:
                discovered = self.search_provider(query)
                with self.database.session() as session:
                    for item in discovered:
                        existing = session.scalar(
                            select(Instrument).where(
                                Instrument.symbol == item["symbol"],
                                Instrument.exchange == item["exchange"],
                                Instrument.asset_type == item["asset_type"],
                            )
                        )
                        if existing is None:
                            session.add(
                                Instrument(
                                    **{
                                        key: item[key]
                                        for key in (
                                            "symbol",
                                            "name",
                                            "exchange",
                                            "asset_type",
                                            "currency",
                                            "timezone",
                                        )
                                    }
                                )
                            )
                        else:
                            existing.name = item["name"]
                    session.flush()
                source = "yfinance"
            except Exception:
                warning = "远程证券搜索暂不可用，当前仅显示已缓存的证券目录。"
        with self.database.session() as session:
            statement = select(Instrument)
            if query:
                pattern = (
                    "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                )
                statement = statement.where(
                    Instrument.symbol.ilike(pattern, escape="\\")
                    | Instrument.name.ilike(pattern, escape="\\")
                )
            rows = session.scalars(statement.order_by(Instrument.symbol).limit(30)).all()
            return {
                "items": [instrument_dict(row) for row in rows],
                "source": source,
                "quality": "partial" if warning else "complete",
                "warning": warning,
            }

    def add_item(self, list_id: str, instrument_id: str, revision: int) -> dict[str, Any]:
        with self._write() as session:
            group = self._owned(session, list_id)
            self._version(group, revision)
            if session.get(Instrument, instrument_id) is None:
                raise WatchlistError("instrument_not_found", "请先搜索并选择证券", 404)
            existing = session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == list_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
            )
            if existing is None:
                position = session.scalar(
                    select(func.max(WatchlistItem.position)).where(
                        WatchlistItem.watchlist_id == list_id,
                    )
                )
                session.add(
                    WatchlistItem(
                        workspace_id=self.workspace_id,
                        watchlist_id=list_id,
                        instrument_id=instrument_id,
                        position=(position or 0) + 1,
                    )
                )
                group.revision += 1
                session.flush()
            return self._render(session, group)

    def remove_item(self, list_id: str, item_id: str, revision: int) -> dict[str, Any]:
        with self._write() as session:
            group = self._owned(session, list_id)
            self._version(group, revision)
            item = session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.id == item_id,
                    WatchlistItem.watchlist_id == list_id,
                )
            )
            if item is None:
                raise WatchlistError("not_found", "自选项不存在", 404)
            session.delete(item)
            group.revision += 1
            session.flush()
            return self._render(session, group)

    def reorder_items(self, list_id: str, item_ids: list[str], revision: int) -> dict[str, Any]:
        with self._write() as session:
            group = self._owned(session, list_id)
            self._version(group, revision)
            items = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == list_id,
                )
            ).all()
            if len(set(item_ids)) != len(item_ids) or set(item_ids) != {item.id for item in items}:
                raise WatchlistError("invalid_order", "排序必须包含本分组每个自选项且不重复")
            positions = {item_id: index for index, item_id in enumerate(item_ids)}
            for item in items:
                item.position = positions[item.id]
            group.revision += 1
            session.flush()
            return self._render(session, group)
