"""Watchlists survive restart and reject stale writes or another owner's IDs."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tradingagents.web.api.watchlists import create_watchlist_router
from tradingagents.web.application.watchlists import WatchlistError, WatchlistService
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, User, Workspace


def search_fixture(_query):
    return [
        {
            "symbol": symbol,
            "name": name,
            "exchange": "NMS",
            "asset_type": "stock",
            "currency": "USD",
            "timezone": "America/New_York",
        }
        for symbol, name in [("NVDA", "NVIDIA"), ("TSLA", "Tesla")]
    ]


@pytest.fixture
def watchlists(tmp_path):
    database = initialize_database(tmp_path / "web.db")
    local = database.local_workspace()
    service = WatchlistService(
        database,
        workspace_id=local.workspace_id,
        owner_id=local.user_id,
        search_provider=search_fixture,
    )
    yield service, database
    database.dispose()


def test_add_deduplicate_reorder_remove_and_persist(watchlists):
    service, database = watchlists
    group = service.list_watchlists()[0]
    ids = [item["id"] for item in service.search_instruments("")["items"]]
    assert ids == []
    ids = [item["id"] for item in service.search_instruments("A")["items"]]
    for instrument_id in ids:
        group = service.add_item(group["id"], instrument_id, group["revision"])
    assert len(group["items"]) == 2
    unchanged = service.add_item(group["id"], ids[0], group["revision"])
    assert unchanged["revision"] == group["revision"]
    item_ids = [item["id"] for item in group["items"]]
    group = service.reorder_items(group["id"], item_ids[::-1], group["revision"])
    assert [item["id"] for item in group["items"]] == item_ids[::-1]
    group = service.remove_item(group["id"], item_ids[0], group["revision"])
    assert len(group["items"]) == 1
    restored = WatchlistService(
        database,
        workspace_id=service.workspace_id,
        owner_id=service.owner_id,
        search_provider=search_fixture,
    )
    assert restored.list_watchlists()[0] == group


def test_deleted_default_group_is_not_recreated_on_restart(watchlists):
    service, database = watchlists
    original = service.list_watchlists()[0]
    replacement = service.create_watchlist("财报关注")
    service.delete_watchlist(original["id"], original["revision"])
    path = database.path
    database.dispose()
    restarted = initialize_database(path)
    try:
        local = restarted.local_workspace()
        assert local.watchlist_id == replacement["id"]
        restored = WatchlistService(
            restarted, workspace_id=local.workspace_id, owner_id=local.user_id
        )
        assert [item["name"] for item in restored.list_watchlists()] == ["财报关注"]
        with pytest.raises(WatchlistError, match="至少保留"):
            restored.delete_watchlist(replacement["id"], replacement["revision"])
    finally:
        restarted.dispose()


def test_stale_and_cross_scope_changes_are_rejected(watchlists):
    service, database = watchlists
    original = service.list_watchlists()[0]
    service.rename_watchlist(original["id"], "核心自选", original["revision"])
    with pytest.raises(WatchlistError) as stale:
        service.rename_watchlist(original["id"], "过期修改", original["revision"])
    assert stale.value.status_code == 409
    outsider = WatchlistService(
        database, workspace_id=service.workspace_id, owner_id="someone-else"
    )
    with pytest.raises(WatchlistError) as forbidden:
        outsider.rename_watchlist(original["id"], "越权修改", 2)
    assert forbidden.value.status_code == 404


def test_two_concurrent_writes_do_not_lose_revision(watchlists):
    service, _database = watchlists
    group = service.list_watchlists()[0]
    barrier = Barrier(2)

    def rename(name):
        barrier.wait(timeout=5)
        try:
            service.rename_watchlist(group["id"], name, group["revision"])
            return "saved"
        except WatchlistError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rename, ["观察 A", "观察 B"]))
    assert sorted(results) == ["revision_conflict", "saved"]
    assert service.list_watchlists()[0]["revision"] == 2


def test_search_failure_is_explicit_and_never_leaks_provider_error(watchlists):
    service, _database = watchlists
    service.search_instruments("NVDA")

    def fail(_query):
        raise RuntimeError("Bearer PRIVATE-KEY /private/path")

    service.search_provider = fail
    result = service.search_instruments("NVDA")
    assert result["quality"] == "partial" and result["source"] == "local_catalog"
    assert result["items"][0]["symbol"] == "NVDA"
    assert "PRIVATE-KEY" not in str(result)


def test_http_contract_validates_group_names_and_mutations(watchlists):
    service, _database = watchlists
    app = FastAPI()
    app.include_router(create_watchlist_router(lambda: service))
    with TestClient(app) as client:
        assert client.post("/api/v1/watchlists", json={"name": " "}).status_code == 422
        group = client.get("/api/v1/watchlists").json()[0]
        instrument = client.get("/api/v1/instruments/search", params={"q": "NVDA"}).json()["items"][
            0
        ]
        added = client.post(
            f"/api/v1/watchlists/{group['id']}/items",
            json={
                "instrument_id": instrument["id"],
                "revision": group["revision"],
            },
        )
        assert added.status_code == 200 and len(added.json()["items"]) == 1
        assert (
            client.patch(
                f"/api/v1/watchlists/{group['id']}",
                json={
                    "name": "过期修改",
                    "revision": group["revision"],
                },
            ).status_code
            == 409
        )


def follow_fixture_stock(service):
    instrument = service.search_instruments("NVDA")["items"][0]
    group = service.list_watchlists()[0]
    group = service.add_item(group["id"], instrument["id"], group["revision"])
    return instrument, group


def test_observations_are_shared_across_groups_and_persist_independently_of_group_revision(
    watchlists,
):
    service, database = watchlists
    instrument, first = follow_fixture_stock(service)
    second = service.create_watchlist("another group")
    second = service.add_item(second["id"], instrument["id"], second["revision"])
    assert first["items"][0]["favorite"] is False
    assert first["items"][0]["notes"] == "" and first["items"][0]["observation_revision"] == 0
    favorite = service.update_observation(instrument["id"], {"revision": 0, "favorite": True})
    assert favorite["revision"] == 1 and favorite["favorite"] and favorite["notes"] == ""
    notes = "等待财报后观察\n\n保持自己的研究记录。"
    updated = service.update_observation(instrument["id"], {"revision": 1, "notes": notes})
    assert updated["revision"] == 2 and updated["favorite"] and updated["notes"] == notes
    assert updated["updated_at"].tzinfo is not None
    groups = service.list_watchlists()
    assert [group["revision"] for group in groups] == [first["revision"], second["revision"]]
    assert all(group["items"][0]["favorite"] for group in groups)
    assert all(group["items"][0]["notes"] == notes for group in groups)
    assert all(group["items"][0]["observation_revision"] == 2 for group in groups)
    restarted = WatchlistService(
        database, workspace_id=service.workspace_id, owner_id=service.owner_id
    )
    assert restarted.list_watchlists() == groups
    cleared = service.update_observation(
        instrument["id"], {"revision": 2, "notes": "", "favorite": False}
    )
    assert cleared["notes"] == "" and not cleared["favorite"] and cleared["revision"] == 3


def test_removing_and_undoing_follow_or_deleting_group_preserves_observation(watchlists):
    service, _database = watchlists
    instrument, group = follow_fixture_stock(service)
    service.update_observation(
        instrument["id"], {"revision": 0, "favorite": True, "notes": "keep this note"}
    )
    group = service.remove_item(group["id"], group["items"][0]["id"], group["revision"])
    with pytest.raises(WatchlistError) as unfollowed:
        service.update_observation(instrument["id"], {"revision": 1, "notes": "not allowed"})
    assert unfollowed.value.code == "instrument_not_watched"
    group = service.add_item(group["id"], instrument["id"], group["revision"])
    assert group["items"][0]["favorite"] and group["items"][0]["notes"] == "keep this note"
    assert group["items"][0]["observation_revision"] == 1
    replacement = service.create_watchlist("replacement")
    service.delete_watchlist(group["id"], group["revision"])
    replacement = service.add_item(replacement["id"], instrument["id"], replacement["revision"])
    assert replacement["items"][0]["notes"] == "keep this note"


def test_observation_first_write_cas_is_serialized_across_concurrent_requests(watchlists):
    service, _database = watchlists
    instrument, group = follow_fixture_stock(service)
    barrier = Barrier(2)

    def update(notes):
        barrier.wait(timeout=5)
        try:
            service.update_observation(instrument["id"], {"revision": 0, "notes": notes})
            return "saved"
        except WatchlistError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["first", "second"]))
    assert sorted(results) == ["observation_revision_conflict", "saved"]
    current = service.list_watchlists()[0]
    assert current["revision"] == group["revision"]
    assert current["items"][0]["observation_revision"] == 1
    assert current["items"][0]["notes"] in {"first", "second"}
    with pytest.raises(WatchlistError) as stale:
        service.update_observation(instrument["id"], {"revision": 0, "favorite": True})
    assert stale.value.status_code == 409


def test_observations_are_private_by_owner_and_workspace_and_require_followed_instrument(
    watchlists,
):
    service, database = watchlists
    instrument, _group = follow_fixture_stock(service)
    service.update_observation(instrument["id"], {"revision": 0, "notes": "original owner"})
    with database.session() as session:
        session.add(User(id="other-owner", name="other"))
        session.flush()
        session.add(
            Membership(workspace_id=service.workspace_id, user_id="other-owner", role="researcher")
        )
        session.add(
            Workspace(id="other-workspace", owner_id=service.owner_id, name="other workspace")
        )
        session.flush()
        session.add(
            Membership(workspace_id="other-workspace", user_id=service.owner_id, role="owner")
        )
    other_owner = WatchlistService(
        database, workspace_id=service.workspace_id, owner_id="other-owner"
    )
    with pytest.raises(WatchlistError) as not_watched:
        other_owner.update_observation(instrument["id"], {"revision": 0, "notes": "overwrite"})
    assert not_watched.value.status_code == 404
    for isolated in (
        other_owner,
        WatchlistService(database, workspace_id="other-workspace", owner_id=service.owner_id),
    ):
        group = isolated.create_watchlist("private list")
        group = isolated.add_item(group["id"], instrument["id"], group["revision"])
        assert group["items"][0]["notes"] == "" and group["items"][0]["observation_revision"] == 0
        isolated.update_observation(instrument["id"], {"revision": 0, "notes": "isolated"})
    assert service.list_watchlists()[0]["items"][0]["notes"] == "original owner"


def test_observation_membership_and_write_role_are_required(watchlists):
    service, database = watchlists
    instrument, _group = follow_fixture_stock(service)
    missing_member = WatchlistService(
        database, workspace_id=service.workspace_id, owner_id="missing"
    )
    with pytest.raises(WatchlistError) as absent:
        missing_member.update_observation(instrument["id"], {"revision": 0, "notes": "private"})
    assert absent.value.status_code == 404
    with database.session() as session:
        membership = session.scalar(
            select(Membership).where(
                Membership.workspace_id == service.workspace_id,
                Membership.user_id == service.owner_id,
            )
        )
        membership.role = "viewer"
    with pytest.raises(WatchlistError) as viewer:
        service.update_observation(instrument["id"], {"revision": 0, "favorite": True})
    assert viewer.value.status_code == 403


def test_observation_http_patch_validates_partial_fields_and_cas(watchlists):
    service, _database = watchlists
    instrument, group = follow_fixture_stock(service)
    app = FastAPI()
    app.include_router(create_watchlist_router(lambda: service))
    with TestClient(app) as client:
        path = "/api/v1/watchlist-observations/" + instrument["id"]
        for invalid in (
            {"revision": 0},
            {"revision": -1, "favorite": True},
            {"revision": True, "favorite": True},
            {"revision": 0, "favorite": "true"},
            {"revision": 0, "favorite": None},
            {"revision": 0, "notes": None},
            {"revision": 0, "notes": "a" * 2001},
            {"revision": 0, "notes": "ok", "owner_id": "different"},
        ):
            assert client.patch(path, json=invalid).status_code == 422
        created = client.patch(path, json={"revision": 0, "favorite": True, "notes": "a" * 2000})
        assert created.status_code == 200
        assert created.json()["revision"] == 1 and created.json()["favorite"]
        assert client.patch(path, json={"revision": 0, "notes": "stale"}).status_code == 409
        edited = client.patch(path, json={"revision": 1, "notes": ""})
        assert (
            edited.status_code == 200 and edited.json()["favorite"] and edited.json()["notes"] == ""
        )
        rendered = client.get("/api/v1/watchlists").json()[0]
        assert rendered["revision"] == group["revision"]
        assert rendered["items"][0]["observation_revision"] == 2
