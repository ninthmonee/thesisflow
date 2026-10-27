"""Offline regressions for account service leases and bounded research admission."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import RLock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tradingagents.web.api.auth_router import create_auth_router
from tradingagents.web.api.auth_schemas import AuthRegisterRequest, AuthSetupRequest
from tradingagents.web.application.authentication import SESSION_COOKIE, AuthenticationService
from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.user_services import UserServiceRegistry, build_user_services
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.http_auth import (
    AccountAuthenticationMiddleware,
    current_request_identity,
)
from tradingagents.web.infrastructure.models import Membership, ResearchRun, User, Workspace
from tradingagents.web.infrastructure.seeds import LocalWorkspace

BASE = "http://127.0.0.1:8000"
PASSWORD = "isolated regression password 123"
DAY = "2024-01-02"
SERVICE_NAMES = (
    "tracking_service", "themes_service", "intelligence_service", "professional_pulse_service"
)


class ManagedService:
    def __init__(self):
        self._pending = {}
        self._lock = RLock()
        self.close_count = 0

    def close(self):
        self.close_count += 1


def bundle():
    return SimpleNamespace(**{name: ManagedService() for name in SERVICE_NAMES})


def workspace(label):
    return LocalWorkspace(str(uuid4()), str(uuid4()), "", label)


def assert_closed(value, count):
    assert all(getattr(value, name).close_count == count for name in SERVICE_NAMES)


def test_registry_reuses_bundle_and_evicts_only_unleased_lru_account():
    local, first, second, third = [workspace(label) for label in ("local", "one", "two", "three")]
    original = bundle()
    created = []

    def factory(_scope):
        value = bundle()
        created.append(value)
        return value

    registry = UserServiceRegistry(local, original, factory, capacity=3)
    try:
        one, two = registry.get(first), registry.get(second)
        assert registry.get(first) is one
        with registry.lease(first):
            three = registry.get(third)
            assert registry.get(first) is one
            assert registry.get(third) is three
            assert_closed(two, 1)
            assert_closed(one, 0)
            assert_closed(original, 0)
        assert len(registry.items) == 3 and len(created) == 3
        assert registry.get(local) is original
    finally:
        registry.close()
    assert_closed(original, 1)
    assert all(getattr(item, name).close_count == 1 for item in created for name in SERVICE_NAMES)


def test_registry_lease_is_released_on_exception_and_does_not_evict_active_request():
    local, first, second = [workspace(label) for label in ("local", "one", "two")]
    registry = UserServiceRegistry(local, bundle(), lambda _: bundle(), capacity=2)
    try:
        with pytest.raises(ValueError, match="handler failed"), registry.lease(first) as managed:
            with registry.lease(first) as again:
                assert again is managed
                with pytest.raises(ResearchError) as captured:
                    registry.get(second)
                assert captured.value.code == "user_services_busy"
                assert captured.value.status_code == 503
                assert_closed(managed, 0)
            raise ValueError("handler failed")
        registry.get(second)
        assert_closed(managed, 1)
        assert all(active == 0 for active in registry.users.values())
    finally:
        registry.close()


@pytest.mark.parametrize("name,shape", [
    ("themes_service", lambda future: {"NVDA": future}),
    ("tracking_service", lambda future: {"instrument-id": (2, future)}),
    ("intelligence_service", lambda future: {future: "NVDA"}),
    ("professional_pulse_service", lambda future: {"query": (datetime.now(UTC), future)}),
])
def test_running_background_future_keeps_real_service_container_shapes_pinned(name, shape):
    local, first, second = [workspace(label) for label in ("local", "one", "two")]
    registry = UserServiceRegistry(local, bundle(), lambda _: bundle(), capacity=2)
    running = Future()
    assert running.set_running_or_notify_cancel()
    try:
        managed = registry.get(first)
        getattr(managed, name)._pending = shape(running)
        with pytest.raises(ResearchError) as captured:
            registry.get(second)
        assert captured.value.status_code == 503
        assert not running.done()
        assert_closed(managed, 0)
        running.set_result({})
        replacement = registry.get(second)
        assert replacement is not managed
        assert_closed(managed, 1)
        assert len(registry.items) == 2
    finally:
        if not running.done():
            running.set_result({})
        registry.close()


def test_concurrent_account_requests_share_one_service_bundle():
    local, first = workspace("local"), workspace("one")
    creations = []

    def factory(_scope):
        value = bundle()
        creations.append(value)
        return value

    registry = UserServiceRegistry(local, bundle(), factory, capacity=2)

    def read(_index):
        with registry.lease(first) as managed:
            return managed

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(read, range(24)))
        assert len(creations) == 1
        assert all(item is creations[0] for item in results)
        assert all(active == 0 for active in registry.users.values())
    finally:
        registry.close()


def test_partial_service_construction_closes_created_resources(monkeypatch, tmp_path):
    closed = []

    class PartialResource:
        def __init__(self, label):
            self.label = label

        def close(self):
            closed.append(self.label)

    def failing_factory(*_args, resources, **_kwargs):
        resources.extend([PartialResource("first"), PartialResource("second")])
        raise RuntimeError("offline construction failed")

    monkeypatch.setattr("tradingagents.web.application.user_services._build_user_services", failing_factory)
    with pytest.raises(RuntimeError, match="offline construction failed"):
        build_user_services(None, workspace("unused"), tmp_path, {})
    assert closed == ["second", "first"]


@pytest.fixture
def account_runtime(tmp_path):
    database = initialize_database(tmp_path / "accounts.sqlite3")
    authentication = AuthenticationService(database,
        password_hasher=PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
    setup_token = authentication.ensure_setup_token().read_text().strip()
    owner = authentication.setup_owner(AuthSetupRequest(
        username="owner", password=PASSWORD, setup_token=setup_token), client_key="owner-fixture")
    first = authentication.register(AuthRegisterRequest(username="first", password=PASSWORD),
        client_key="first-fixture")
    second = authentication.register(AuthRegisterRequest(username="second", password=PASSWORD),
        client_key="second-fixture")
    try:
        yield SimpleNamespace(database=database, authentication=authentication,
            owner=owner, first=first, second=second)
    finally:
        database.dispose()


def test_full_business_service_pool_returns_503_but_account_logout_still_works(account_runtime):
    runtime = account_runtime
    local = runtime.authentication.authenticate(runtime.owner.token).workspace
    first_scope = runtime.authentication.authenticate(runtime.first.token).workspace
    registry = UserServiceRegistry(local, bundle(), lambda _: bundle(), capacity=2)
    app = FastAPI()
    app.include_router(create_auth_router(lambda: runtime.authentication))
    app.add_middleware(AccountAuthenticationMiddleware, authentication=runtime.authentication,
        service_registry=registry)
    called = []

    @app.get("/api/v1/private-scope")
    def private_scope():
        identity = current_request_identity().identity
        called.append(identity.user_id)
        return {"workspace_id": identity.workspace.workspace_id}

    pending = Future()
    assert pending.set_running_or_notify_cancel()
    managed = registry.get(first_scope)
    managed.intelligence_service._pending = {pending: "NVDA"}
    try:
        with TestClient(app, base_url=BASE) as client:
            client.cookies.set(SESSION_COOKIE, runtime.second.token)
            second = runtime.second.status
            headers = {"Origin": BASE, "X-CSRF-Token": second.csrf_token,
                "X-Workspace-ID": second.user.workspace.id}
            response = client.get("/api/v1/private-scope", headers=headers)
            assert response.status_code == 503
            assert response.json() == {"error_code": "user_services_busy",
                "message": "服务正忙，请稍后重试。", "retryable": True}
            assert called == []
            assert client.get("/api/v1/auth/session").json()["authenticated"] is True
            response = client.post("/api/v1/auth/logout", headers=headers)
            assert response.status_code == 200 and response.json()["authenticated"] is False
            assert client.get("/api/v1/private-scope", headers=headers).status_code == 401
            assert runtime.authentication.session_status(runtime.second.token).authenticated is False
            assert runtime.authentication.session_status(runtime.first.token).authenticated is True
            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE, runtime.owner.token)
            assert client.get("/api/v1/private-scope").json() == {"workspace_id": local.workspace_id}
            assert called == [local.user_id]
        assert current_request_identity() is None
        assert_closed(managed, 0)
    finally:
        pending.set_result({})
        registry.close()


@pytest.fixture
def bounded_research(tmp_path):
    database = initialize_database(tmp_path / "queue.sqlite3")
    local = database.local_workspace()
    friend = workspace("friend")
    with database.session() as session:
        session.add(User(id=friend.user_id, name="Friend"))
        session.flush()
        session.add(Workspace(id=friend.workspace_id, owner_id=friend.user_id, name="Private"))
        session.flush()
        session.add(Membership(workspace_id=friend.workspace_id, user_id=friend.user_id, role="owner"))
    service = ResearchService(database, tmp_path / "research", max_active_runs_per_owner=3,
        max_queued_runs_total=50, data_snapshot_provider=lambda *_: {"providers": {}})
    scopes = [{"workspace_id": item.workspace_id, "owner_id": item.user_id} for item in (local, friend)]
    try:
        yield service, scopes
    finally:
        database.dispose()


def queue_run(service, scope, key, ticker="NVDA"):
    return service.create_run({"ticker": ticker, "date": DAY}, idempotency_key=key, **scope)


def test_full_account_queue_accepts_identical_retry_and_keeps_other_user_independent(bounded_research):
    service, (owner, friend) = bounded_research
    runs = [queue_run(service, owner, f"request-{index}") for index in range(3)]
    assert queue_run(service, owner, "request-0")["id"] == runs[0]["id"]
    with pytest.raises(ResearchError) as captured:
        queue_run(service, owner, "fourth")
    assert (captured.value.code, captured.value.status_code) == ("research_account_queue_full", 429)
    separate = queue_run(service, friend, "request-0", ticker="AAPL")
    assert separate["id"] not in {item["id"] for item in runs}
    assert {item["id"] for item in service.list_runs(**owner)["items"]} == {item["id"] for item in runs}
    assert [item["id"] for item in service.list_runs(**friend)["items"]] == [separate["id"]]
    with pytest.raises(ResearchError) as foreign:
        service.get_run(runs[0]["id"], **friend)
    assert foreign.value.status_code == 404
    assert not service.artifacts_root.exists()


def test_full_global_queue_accepts_existing_idempotent_retry_but_rejects_new_work(bounded_research):
    service, (owner, friend) = bounded_research
    service.max_queued_runs_total = 4
    for index in range(3):
        queue_run(service, owner, f"owner-{index}")
    first = queue_run(service, friend, "friend-first")
    assert queue_run(service, friend, "friend-first")["id"] == first["id"]
    with pytest.raises(ResearchError) as captured:
        queue_run(service, friend, "friend-next")
    assert (captured.value.code, captured.value.status_code) == ("research_queue_full", 429)
    with pytest.raises(ResearchError) as conflict:
        queue_run(service, friend, "friend-first", ticker="AMD")
    assert conflict.value.code == "idempotency_conflict"


def test_concurrent_submissions_cannot_overfill_three_run_account_limit(bounded_research):
    service, (owner, _friend) = bounded_research

    def submit(index):
        try:
            return queue_run(service, owner, f"parallel-{index}")["id"]
        except ResearchError as error:
            assert (error.code, error.status_code) == ("research_account_queue_full", 429)
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(12)))
    assert len({item for item in results if item}) == 3
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 3


def test_concurrent_idempotent_retries_remain_valid_when_account_queue_is_full(bounded_research):
    service, (owner, _friend) = bounded_research
    first = queue_run(service, owner, "retry")
    queue_run(service, owner, "other-one")
    queue_run(service, owner, "other-two")
    with ThreadPoolExecutor(max_workers=6) as pool:
        repeated = list(pool.map(lambda _: queue_run(service, owner, "retry")["id"], range(12)))
    assert repeated == [first["id"]] * 12
    assert len(service.list_runs(**owner)["items"]) == 3


def test_running_and_cancelling_runs_keep_account_slot_until_terminal(bounded_research):
    service, (owner, _friend) = bounded_research
    first = queue_run(service, owner, "first")
    queue_run(service, owner, "second")
    queue_run(service, owner, "third")
    claim = service.tasks.claim_next("offline-fixture")
    assert claim.run_id == first["id"]
    assert service.cancel_run(first["id"], **owner)["status"] == "cancelling"
    with pytest.raises(ResearchError) as captured:
        queue_run(service, owner, "fourth")
    assert captured.value.code == "research_account_queue_full"
    service.tasks.finish(claim, "cancelled")
    assert queue_run(service, owner, "fourth")["status"] == "queued"
