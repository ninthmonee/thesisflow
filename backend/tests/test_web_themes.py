"""Theme configuration is scoped/versioned; analytics retain actual sample/data limits."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from exchange_calendars import get_calendar
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from tradingagents.web.api.themes_router import create_themes_router
from tradingagents.web.api.themes_schemas import ThemeError, ThemeMemberInput, ThemeWrite
from tradingagents.web.application.themes import ThemesService
from tradingagents.web.domain.theme_catalog import LEGACY_MEMORY_CATALOG, THEME_CATALOG
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, Workspace
from tradingagents.web.infrastructure.theme_model import ThemeRecord, ThemeVersionRecord


class Market:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.block = None
        days = [
            value.strftime("%Y-%m-%d")
            for value in get_calendar("XNYS").sessions_in_range("2026-08-03", "2026-10-23")
        ]
        for symbol, multiplier in (("SPY", 1), ("AAA", 2), ("BBB", 3), ("CCC", -0.3), ("DDD", 1.5)):
            self.responses[symbol] = {
                "symbol": symbol,
                "interval": "1d",
                "period": "3mo",
                "session": "regular",
                "adjustment": "provider_native",
                "bars": [
                    {
                        "date": day,
                        "close": 100 + index * multiplier,
                        "volume": 1000 + index,
                        "is_complete": True,
                    }
                    for index, day in enumerate(days)
                ],
                "meta": {"source": "fixture", "feed": "fixture-daily", "quality": "complete"},
            }

    def get_bars(self, symbol, **kwargs):
        self.calls.append(symbol)
        if self.block:
            self.block.wait(2)
        if symbol not in self.responses:
            raise RuntimeError("secret upstream details")
        return deepcopy(self.responses[symbol])


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "database.sqlite3")
    local = database.local_workspace()
    market = Market()
    service = ThemesService(
        database,
        local,
        market,
        clock=lambda: datetime(2026, 10, 24, 21, tzinfo=UTC),
        timeout_seconds=0.05,
    )
    yield service, database, local, market
    service.close()
    database.dispose()


def basket(service, symbols=("AAA", "BBB", "CCC"), name="观察主题"):
    return service.create_theme(
        {"name": name, "members": [{"symbol": symbol, "name": symbol} for symbol in symbols]}
    )


def fields(view):
    result = view.model_dump(include=set(ThemeWrite.model_fields))
    result["members"] = [
        member.model_dump(include=set(ThemeMemberInput.model_fields)) for member in view.members
    ]
    return result


def legacy_memory_pair(service, database):
    """Represent a database seeded before memory consolidation and pin preferences."""
    with database.session() as session:
        result = []
        for entry in LEGACY_MEMORY_CATALOG:
            body = ThemeWrite.model_validate(
                {key: value for key, value in entry.items() if key != "key"}
            )
            record = service._new(session, body, entry["key"])
            original = service._version(session, record)
            original.configuration = {
                key: value
                for key, value in original.configuration.items()
                if key != "_record_revision"
            }
            result.append(service._view(session, record))
        return result


def test_startup_seeds_once_and_never_overwrites_user_edits_or_archive(context):
    service, _db, _local, market = context
    assert service.list_themes()["items"] == [] and market.calls == []
    service.ensure_defaults()
    first = service.list_themes()["items"]
    assert len(first) == len(THEME_CATALOG)
    names = {row.name for row in first}
    assert {"磷化铟 InP", "mRNA药物与疫苗", "氦气", "光纤/光缆/连接"} <= names
    inp = next(row for row in first if row.name == "磷化铟 InP")
    assert [row.symbol for row in inp.members] == ["AXTI", "COHR", "LITE"]
    assert all(row.evidence_urls and row.verified_at for row in inp.members)
    assert any(not row.members for row in first)
    updated = service.update_theme(inp.id, {**fields(inp), "name": "我的InP", "revision": 1})
    service.archive_theme(updated.id, updated.revision)
    service.ensure_defaults()
    assert len(service.list_themes()["items"]) == len(THEME_CATALOG) - 1
    assert len(service.list_themes(include_archived=True)["items"]) == len(THEME_CATALOG)
    assert service.get_theme(inp.id).name == "我的InP"
    assert market.calls == []


def test_ai_starter_themes_separate_chips_networks_and_diversified_cloud_businesses():
    catalog = {
        row["key"]: ThemeWrite.model_validate(
            {key: value for key, value in row.items() if key != "key"}
        )
        for row in THEME_CATALOG
    }
    chips = catalog["ai-training-inference-chips"].members
    assert [row.symbol for row in chips] == ["NVDA", "AMD", "AVGO"]
    assert "GPU" in chips[0].role and "ASIC" in chips[2].role
    network = catalog["ai-networking-switching"].members
    assert [row.symbol for row in network] == ["AVGO", "ANET"]
    cloud = catalog["ai-cloud-model-infrastructure"].members
    assert [row.symbol for row in cloud] == ["MSFT", "AMZN", "GOOG"]
    assert all(row.exposure == "diversified_business" for row in cloud)
    assert all(
        row.evidence_urls and row.verified_at == "2026-10-25" for row in (*chips, *network, *cloud)
    )


def test_current_snapshot_is_persistent_and_versions_immutable(context, tmp_path):
    service, database, local, market = context
    original = basket(service)
    edited = service.remove_member(original.id, "BBB", original.revision)
    assert edited.version == edited.revision == 2
    assert [row.symbol for row in edited.members] == ["AAA", "CCC"]
    history = service.list_versions(original.id)["items"]
    assert [row.version for row in history] == [2, 1]
    assert [row.symbol for row in history[1].members] == ["AAA", "BBB", "CCC"]
    restarted = ThemesService(database, local, market)
    try:
        assert restarted.get_theme(original.id).version == 2
    finally:
        restarted.close()
    with database.session() as session:
        assert len(session.scalars(select(ThemeVersionRecord)).all()) == 2


def test_compare_and_swap_rejects_stale_member_or_theme_write(context):
    service, _db, _local, _market = context
    created = basket(service)
    edited = service.add_member(created.id, {"revision": 1, "member": {"symbol": "DDD"}})
    assert edited.revision == 2
    with pytest.raises(ThemeError) as error:
        service.remove_member(created.id, "AAA", 1)
    assert error.value.code == "theme_revision_conflict"
    assert len(service.get_theme(created.id).members) == 4


def test_manual_relationship_records_are_retained_without_claiming_platform_verification(context):
    service, _db, _local, _market = context
    created = service.create_theme(
        {
            "name": "人工分类",
            "members": [
                {
                    "symbol": "aaa",
                    "exposure": "direct_product",
                    "verified_at": "2026-10-25",
                    "evidence_urls": ["https://company.example/product"],
                }
            ],
        }
    )
    assert created.members[0].symbol == "AAA"
    assert created.members[0].exposure == "direct_product"
    assert created.members[0].verified_at == "2026-10-25"
    assert created.members[0].evidence_quality == "documented"
    assert "不表示平台验证" in created.evidence_notice
    service.ensure_defaults()
    inp = next(row for row in service.list_themes()["items"] if row.name == "磷化铟 InP")
    changed = service.update_theme(
        inp.id,
        {
            **fields(inp),
            "revision": 1,
            "members": [
                {**fields(inp)["members"][0], "role": "user changed"},
                fields(inp)["members"][1],
            ],
        },
    )
    assert changed.members[0].role == "user changed"
    assert changed.members[0].evidence_quality == "documented"
    assert changed.members[1].exposure == "direct_product"


def test_same_symbol_can_be_in_many_themes_but_not_duplicate_in_one(context):
    service, _db, _local, _market = context
    a, b = basket(service, ("AAA",)), basket(service, ("AAA",), name="其他主题")
    assert a.id != b.id
    with pytest.raises(ValidationError):
        basket(service, ("AAA", "AAA"))
    with pytest.raises(ThemeError) as error:
        service.add_member(a.id, {"revision": 1, "member": {"symbol": "AAA"}})
    assert error.value.code == "theme_member_exists"


def test_archive_is_readable_and_clone_keeps_old_history(context):
    service, _db, _local, _market = context
    created = basket(service, ())
    archived = service.archive_theme(created.id, 1)
    assert archived.archived and service.list_themes()["items"] == []
    clone = service.clone_theme(created.id, {"revision": 2, "name": "复制主题"})
    assert clone.id != created.id and not clone.archived and not clone.members
    assert not clone.is_builtin
    assert len(service.list_versions(created.id)["items"]) == 2


def test_pin_persists_metadata_and_unpin_restores_original_creation_order(context):
    service, database, local, market = context
    clock = [datetime(2026, 10, 25, tzinfo=UTC)]
    service.clock = lambda: clock[0]
    first = basket(service, name="first")
    clock[0] += timedelta(seconds=1)
    second = basket(service, name="second")
    clock[0] += timedelta(seconds=1)
    third = basket(service, name="third")
    pinned_second = service.set_theme_pinned(second.id, {"revision": 1, "pinned": True})
    assert pinned_second.pinned and pinned_second.revision == 2 and pinned_second.version == 1
    clock[0] += timedelta(seconds=1)
    pinned_third = service.set_theme_pinned(third.id, {"revision": 1, "pinned": True})
    assert [row.id for row in service.list_themes()["items"]] == [third.id, second.id, first.id]
    restarted = ThemesService(database, local, market)
    try:
        assert restarted.get_theme(second.id).pinned
        assert [row.id for row in restarted.list_themes()["items"]] == [
            third.id,
            second.id,
            first.id,
        ]
    finally:
        restarted.close()
    same = service.set_theme_pinned(second.id, {"revision": 2, "pinned": True})
    assert same.revision == 2  # Repeating the current preference does not reorder pins.
    service.set_theme_pinned(second.id, {"revision": same.revision, "pinned": False})
    assert [row.id for row in service.list_themes()["items"]] == [third.id, first.id, second.id]
    service.set_theme_pinned(third.id, {"revision": pinned_third.revision, "pinned": False})
    assert [row.id for row in service.list_themes()["items"]] == [first.id, second.id, third.id]
    assert len(service.list_versions(second.id)["items"]) == 1
    assert market.calls == []


def test_pin_cas_revision_is_separate_from_immutable_configuration_versions(context):
    service, _database, _local, _market = context
    created = basket(service)
    pinned = service.set_theme_pinned(created.id, {"revision": 1, "pinned": True})
    with pytest.raises(ThemeError) as stale_pin:
        service.set_theme_pinned(created.id, {"revision": 1, "pinned": False})
    assert stale_pin.value.code == "theme_revision_conflict"
    with pytest.raises(ThemeError):
        service.update_theme(created.id, {**fields(created), "revision": 1})
    changed = service.update_theme(created.id, {**fields(pinned), "revision": 2, "name": "changed"})
    assert changed.pinned and changed.version == 2 and changed.revision == 3
    history = service.list_versions(created.id)["items"]
    assert [(row.version, row.revision) for row in history] == [(2, 3), (1, 1)]
    service.set_theme_pinned(created.id, {"revision": 3, "pinned": False})
    assert service.get_theme(created.id).revision == 4
    assert [(row.version, row.revision) for row in service.list_versions(created.id)["items"]] == [
        (2, 3),
        (1, 1),
    ]


def test_restore_preserves_identity_members_history_and_pin_without_reseeding(context):
    service, _database, _local, _market = context
    created = basket(service)
    pinned = service.set_theme_pinned(created.id, {"revision": 1, "pinned": True})
    archived = service.archive_theme(created.id, pinned.revision)
    assert archived.archived and archived.pinned and service.list_themes()["items"] == []
    with pytest.raises(ThemeError) as stale_restore:
        service.restore_theme(created.id, pinned.revision)
    assert stale_restore.value.code == "theme_revision_conflict"
    restored = service.restore_theme(created.id, archived.revision)
    assert restored.id == created.id and restored.created_at == created.created_at
    assert (
        not restored.archived
        and restored.pinned
        and restored.version == 3
        and restored.revision == 4
    )
    assert (
        restored.members == created.members and service.list_themes()["items"][0].id == created.id
    )
    assert [row.archived for row in service.list_versions(created.id)["items"]] == [
        False,
        True,
        False,
    ]
    assert service.restore_theme(created.id, restored.revision).revision == restored.revision


def test_memory_defaults_are_consolidated_on_fresh_start_with_unique_members(context):
    service, database, _local, _market = context
    service.ensure_defaults()
    items = service.list_themes()["items"]
    storage = next(row for row in items if row.name == "存储 / HBM / DRAM")
    assert [row.symbol for row in storage.members] == ["MU", "SNDK", "WDC", "STX"]
    assert {"HBM", "DRAM"} <= set(storage.aliases)
    assert {"NAND/SSD", "HDD/大容量存储"} <= {row.name for row in items}
    with database.session() as session:
        assert (
            session.scalar(select(ThemeRecord).where(ThemeRecord.builtin_key == "hbm-dram")) is None
        )
    service.ensure_defaults()
    assert service.get_theme(storage.id).version == 1


def test_untouched_legacy_memory_pair_merges_once_keeps_ids_history_and_pin(context):
    service, database, _local, _market = context
    storage, hbm = legacy_memory_pair(service, database)
    pinned = service.set_theme_pinned(hbm.id, {"revision": 1, "pinned": True})
    service.ensure_defaults()
    combined, old_hbm = service.get_theme(storage.id), service.get_theme(hbm.id)
    assert combined.name == "存储 / HBM / DRAM" and combined.members == storage.members
    assert combined.version == 2 and combined.revision == 2 and combined.pinned
    assert old_hbm.archived and old_hbm.pinned and old_hbm.version == 2
    assert old_hbm.revision == pinned.revision + 1
    assert service.list_versions(storage.id)["items"][1].name == storage.name
    assert service.list_versions(hbm.id)["items"][1].members == hbm.members
    service.ensure_defaults()
    assert service.get_theme(storage.id).version == 2 and service.get_theme(hbm.id).version == 2
    restored = service.restore_theme(hbm.id, old_hbm.revision)
    service.ensure_defaults()
    assert service.get_theme(hbm.id).revision == restored.revision
    assert not service.get_theme(hbm.id).archived


@pytest.mark.parametrize("edited_key", ["memory-storage", "hbm-dram"])
def test_legacy_memory_consolidation_skips_either_user_edited_configuration(context, edited_key):
    service, database, _local, _market = context
    storage, hbm = legacy_memory_pair(service, database)
    target = storage if edited_key == "memory-storage" else hbm
    service.update_theme(target.id, {**fields(target), "revision": 1, "name": "my research"})
    service.ensure_defaults()
    assert service.get_theme(target.id).name == "my research"
    assert not service.get_theme(hbm.id).archived
    if edited_key == "hbm-dram":
        assert service.get_theme(storage.id).name == storage.name


def test_memory_upgrade_requires_exact_original_config_even_at_version_one(context):
    service, database, _local, _market = context
    storage, hbm = legacy_memory_pair(service, database)
    with database.session() as session:
        record = session.scalar(select(ThemeRecord).where(ThemeRecord.id == storage.id))
        version = service._version(session, record)
        version.configuration = {**version.configuration, "description": "different source seed"}
    service.ensure_defaults()
    assert service.get_theme(storage.id).name == storage.name
    assert not service.get_theme(hbm.id).archived


def test_builtin_seed_updates_cannot_exhaust_or_be_blocked_by_custom_theme_quota(context):
    service, database, _local, _market = context
    with database.session() as session:
        for index in range(199):
            service._new(session, ThemeWrite(name=f"custom-{index}"))
    service.ensure_defaults()
    last = service.create_theme({"name": "custom-200"})
    service.archive_theme(last.id, last.revision)
    service.ensure_defaults()  # A full custom quota cannot prevent application startup.
    assert len(service.list_themes(include_archived=True)["items"]) == 200 + len(THEME_CATALOG)
    with pytest.raises(ThemeError) as create_error:
        service.create_theme({"name": "custom-201"})
    assert create_error.value.code == "theme_limit"
    builtin = next(row for row in service.list_themes()["items"] if row.is_builtin)
    with pytest.raises(ThemeError) as clone_error:
        service.clone_theme(builtin.id, {"revision": builtin.revision, "name": "one more custom"})
    assert clone_error.value.code == "theme_limit"


def test_cross_workspace_and_viewer_mutation_are_blocked(context):
    service, database, local, market = context
    created = basket(service)
    with database.session() as session:
        session.add(Workspace(id="other", owner_id=local.user_id, name="other"))
        session.flush()
        session.add(Membership(workspace_id="other", user_id=local.user_id, role="owner"))
    other = ThemesService(database, replace(local, workspace_id="other"), market)
    try:
        with pytest.raises(ThemeError) as error:
            other.get_theme(created.id)
        assert error.value.status_code == 404
        with pytest.raises(ThemeError) as pin_error:
            other.set_theme_pinned(created.id, {"revision": 1, "pinned": True})
        assert pin_error.value.status_code == 404
        with pytest.raises(ThemeError) as restore_error:
            other.restore_theme(created.id, 1)
        assert restore_error.value.status_code == 404
        assert other.list_themes()["items"] == []
    finally:
        other.close()
    with database.session() as session:
        role = session.scalar(
            select(Membership).where(Membership.workspace_id == local.workspace_id)
        )
        role.role = "viewer"
    assert service.get_theme(created.id).id == created.id
    with pytest.raises(ThemeError) as error:
        basket(service)
    assert error.value.status_code == 403
    with pytest.raises(ThemeError) as pin_error:
        service.set_theme_pinned(created.id, {"revision": 1, "pinned": True})
    assert pin_error.value.status_code == 403
    with pytest.raises(ThemeError) as restore_error:
        service.restore_theme(created.id, 1)
    assert restore_error.value.status_code == 403


def test_analytics_use_equal_weight_returns_same_feed_common_dates(context):
    service, _db, _local, market = context
    created = basket(service)
    result = service.get_analytics(created.id)
    assert result.available_count == result.requested_count == 3
    assert result.quality == "complete"
    expected = [
        (
            (
                market.responses[symbol]["bars"][-1]["close"]
                / market.responses[symbol]["bars"][-2]["close"]
            )
            - 1
        )
        * 100
        for symbol in ("AAA", "BBB", "CCC")
    ]
    assert result.return_percent == pytest.approx(sum(expected) / 3)
    assert result.relative_return_pp == pytest.approx(
        result.return_percent - result.spy_return_percent
    )
    assert result.advancers == 2 and result.decliners == 1
    assert all(row.rvol_sample_count == 20 and row.daily_rvol for row in result.members)
    assert "净流入" in result.turnover_method
    assert "所选主题版本" in result.membership_basis
    assert not hasattr(result, "total_volume")
    before = len(market.calls)
    service.get_analytics(created.id)
    assert len(market.calls) == before


def test_incomplete_bars_and_missing_endpoints_are_not_used(context):
    service, _db, _local, market = context
    for response in market.responses.values():
        response["bars"].append(
            {"date": "2026-10-26", "close": 9999, "volume": 999999, "is_complete": False}
        )
    market.responses["BBB"]["bars"] = market.responses["BBB"]["bars"][:-2]
    result = service.get_analytics(basket(service).id)
    assert result.window_end == "2026-10-23"
    assert result.available_count == 2 and result.quality == "partial"
    assert next(row for row in result.members if row.symbol == "BBB").return_percent is None
    assert result.return_percent is not None


def test_mixed_feed_and_missing_symbols_never_become_zero_return(context):
    service, _db, _local, market = context
    market.responses["BBB"]["meta"]["feed"] = "different-exchange"
    result = service.get_analytics(basket(service, ("AAA", "BBB", "MISSING")).id)
    assert result.available_count == 1
    by_symbol = {row.symbol: row for row in result.members}
    assert by_symbol["BBB"].return_percent is None and "feed" in by_symbol["BBB"].reason
    assert by_symbol["MISSING"].return_percent is None
    assert "secret upstream" not in result.model_dump_json()
    assert result.quality == "partial"


def test_insufficient_rvol_history_is_null_and_zero_volume_valid(context):
    service, _db, _local, market = context
    market.responses["AAA"]["bars"] = market.responses["AAA"]["bars"][-5:]
    market.responses["BBB"]["bars"][-1]["volume"] = 0
    result = service.get_analytics(basket(service).id)
    by_symbol = {row.symbol: row for row in result.members}
    assert by_symbol["AAA"].daily_rvol is None and by_symbol["AAA"].rvol_sample_count == 4
    assert by_symbol["BBB"].daily_rvol == 0 and by_symbol["BBB"].turnover_estimate_usd == 0


@pytest.mark.parametrize("window", ["1D", "5D", "1M"])
def test_window_matches_spy_calendar_not_available_bar_positions(context, window):
    service, _db, _local, market = context
    created = basket(service)
    result = service.get_analytics(created.id, window=window)
    assert result.window_start < result.window_end
    if window == "5D":
        assert result.window_start == "2026-10-16"
    if window == "1M":
        assert result.window_start == "2026-09-23"


def test_empty_theme_returns_unavailable_without_synthetic_members(context):
    service, _db, _local, market = context
    result = service.get_analytics(basket(service, ()).id)
    assert result.requested_count == result.available_count == 0
    assert result.members == [] and result.return_percent is None
    assert result.quality == "unavailable"
    assert market.calls == []


def test_relationship_evidence_is_separate_from_price_quality_and_filter_denominator(context):
    service, _database, _local, market = context
    created = service.create_theme(
        {
            "name": "证据覆盖",
            "members": [
                {
                    "symbol": "AAA",
                    "exposure": "supplier",
                    "verified_at": "2026-10-23",
                    "evidence_urls": ["https://company.example/product"],
                },
                {"symbol": "BBB", "exposure": "direct_product"},
                {
                    "symbol": "CCC",
                    "verified_at": "2026-10-23",
                    "evidence_urls": ["https://company.example/product"],
                },
            ],
        }
    )
    assert [member.evidence_quality for member in created.members] == [
        "documented",
        "incomplete",
        "unverified",
    ]
    documented = service.get_analytics(created.id, documented_only=True)
    assert documented.original_member_count == 3 and documented.excluded_count == 2
    assert documented.requested_count == documented.available_count == 1
    assert [member.symbol for member in documented.members] == ["AAA"]
    assert set(market.calls) == {"SPY", "AAA"}
    all_members = service.get_analytics(created.id)
    assert all_members.requested_count == all_members.available_count == 3
    assert all_members.excluded_count == 0 and all_members.quality == "complete"
    assert all_members.members[1].evidence_quality == "incomplete"
    assert all_members.members[1].quality == "complete"
    empty = basket(service, ("DDD",))
    before = len(market.calls)
    filtered = service.get_analytics(empty.id, documented_only=True)
    assert filtered.requested_count == filtered.available_count == 0
    assert filtered.original_member_count == filtered.excluded_count == 1
    assert len(market.calls) == before


def test_historical_configuration_fixes_members_but_uses_recent_market_window(context):
    service, _database, _local, _market = context
    created = basket(service, ("AAA",))
    updated = service.update_theme(
        created.id,
        {
            **fields(created),
            "revision": 1,
            "members": [{"symbol": "BBB"}],
            "taxonomy_tags": [
                {"kind": "industry", "label": "半导体"},
                {"kind": "material", "label": "InP"},
            ],
        },
    )
    current, historic = (
        service.get_analytics(created.id),
        service.get_analytics(created.id, version=1),
    )
    assert [member.symbol for member in current.members] == ["BBB"]
    assert [member.symbol for member in historic.members] == ["AAA"]
    assert current.version == 2 and historic.version == 1
    assert current.window_end == historic.window_end == "2026-10-23"
    assert historic.window_basis == "latest_completed_sessions"
    assert historic.membership_effective_from == created.effective_from
    assert "不是历史回测" in historic.warnings[0]
    assert service.get_theme(created.id, version=1).taxonomy_tags == []
    assert [tag.kind for tag in updated.taxonomy_tags] == ["industry", "material"]
    with pytest.raises(ThemeError) as absent:
        service.get_theme(created.id, version=99)
    assert absent.value.code == "theme_version_not_found"
    service.archive_theme(created.id, updated.revision)
    with pytest.raises(ThemeError) as archived:
        service.get_analytics(created.id)
    assert archived.value.code == "theme_archived"
    assert service.get_analytics(created.id, version=1).version == 1


def test_taxonomy_tags_keep_dimensions_distinct_and_validate_duplicates(context):
    service, _database, _local, _market = context
    created = service.create_theme(
        {
            "name": "材料",
            "category": "materials",
            "taxonomy_tags": [
                {"kind": "industry", "label": " 半导体 "},
                {"kind": "theme", "label": "半导体"},
                {"kind": "value_chain", "label": "衬底"},
            ],
        }
    )
    assert created.category == "materials" and created.taxonomy_tags[0].label == "半导体"
    for tags in (
        [{"kind": "material", "label": "InP"}, {"kind": "material", "label": " inp "}],
        [{"kind": "sector", "label": "technology"}],
        [{"kind": "theme", "label": " "}],
        [{"kind": "material", "label": "A" * 81}],
        [{"kind": "theme", "label": str(index)} for index in range(25)],
    ):
        with pytest.raises(ValidationError):
            ThemeWrite(name="invalid", taxonomy_tags=tags)


def test_stale_source_remains_stale(context):
    service, _db, _local, market = context
    market.responses["AAA"]["meta"]["quality"] = "stale"
    result = service.get_analytics(basket(service).id)
    assert result.quality == "stale" and result.members[0].quality == "stale"


def test_timeout_is_bounded_and_never_stacks_duplicate_futures(context):
    service, _db, _local, market = context
    market.block = Event()
    created = basket(service)
    try:
        result = service.get_analytics(created.id)
        assert result.quality == "unavailable"
        assert len(service._pending) <= 4
        service.get_analytics(created.id, refresh=True)
        assert len(service._pending) <= 4
        assert len(set(market.calls)) == len(market.calls)
    finally:
        market.block.set()


def test_router_contract_and_error_shapes(context):
    service, _db, _local, _market = context
    app = FastAPI()
    app.include_router(create_themes_router(lambda: service))
    with TestClient(app) as client:
        created = client.post("/api/v1/themes", json={"name": "custom", "members": []})
        assert created.status_code == 201
        identifier = created.json()["id"]
        assert client.get("/api/v1/themes").json()["items"][0]["id"] == identifier
        assert (
            client.get("/api/v1/themes/" + identifier + "/analytics").json()["available_count"] == 0
        )
        assert (
            client.put(
                "/api/v1/themes/" + identifier, json={"name": "new", "revision": 1}
            ).status_code
            == 200
        )
        conflict = client.post(
            "/api/v1/themes/" + identifier + "/members",
            json={"revision": 1, "member": {"symbol": "AAA"}},
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["error_code"] == "theme_revision_conflict"
        )
        assert (
            client.get("/api/v1/themes/" + identifier + "/versions").json()["items"][1]["name"]
            == "custom"
        )
        pinned = client.patch(
            "/api/v1/themes/" + identifier + "/pin", json={"revision": 2, "pinned": True}
        )
        assert pinned.status_code == 200 and pinned.json()["pinned"] is True
        assert pinned.json()["revision"] == 3 and pinned.json()["version"] == 2
        assert (
            client.patch(
                "/api/v1/themes/" + identifier + "/pin", json={"revision": 3, "pinned": "true"}
            ).status_code
            == 422
        )
        archived = client.request("DELETE", "/api/v1/themes/" + identifier, json={"revision": 3})
        assert archived.status_code == 200 and archived.json()["archived"]
        restored = client.post("/api/v1/themes/" + identifier + "/restore", json={"revision": 4})
        assert (
            restored.status_code == 200
            and not restored.json()["archived"]
            and restored.json()["pinned"]
        )
        assert restored.json()["revision"] == 5 and restored.json()["version"] == 4


@pytest.mark.parametrize(
    "value", ["file:///etc/passwd", "https://user:password@example.com", "javascript:alert(1)"]
)
def test_evidence_urls_are_not_file_or_credential_references(value):
    with pytest.raises(ValidationError):
        ThemeMemberInput(symbol="AAA", evidence_urls=[value])


@pytest.mark.parametrize(
    "symbol",
    [
        "ASML.AS",
        "SAP.DE",
        "7203.T",
        "SHOP.TO",
        "RIO.AX",
        "0700.HK",
        "EURUSD=X",
        "GC=F",
        "BTC-USD",
        "^N225",
    ],
)
def test_theme_members_reject_foreign_currency_markets_and_non_equity_products(symbol):
    with pytest.raises(ValidationError, match="当前仅支持美股"):
        ThemeMemberInput(symbol=symbol)


@pytest.mark.parametrize("symbol", ["BRK.A", "BRK.B", "BF.A", "BF.B", "BRK-B", "TSM", "BNTX"])
def test_theme_members_accept_us_share_classes_and_us_listed_adrs(symbol):
    assert ThemeMemberInput(symbol=symbol.lower()).symbol == symbol
