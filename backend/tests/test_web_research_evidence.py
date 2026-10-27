"""Real database/artifact evidence capture and deterministic qualification, offline."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tradingagents.dataflows.credentials import data_credentials_scope
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.evidence import EvidenceCaptureError, evidence_scope, observed_call
from tradingagents.web.api.research_evidence_router import create_research_evidence_router
from tradingagents.web.application.research import ResearchService
from tradingagents.web.application.research_evidence import ResearchEvidenceStore
from tradingagents.web.domain.research_quality import (
    MAX_RESPONSE_CHARS,
    normalize_observation,
    qualify,
)
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import DataSnapshot, Membership, User, Workspace

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
MARKET = "# Stock data\nDate,Open,High,Low,Close,Volume\n2024-01-02,100,110,95,105,1000\n"


def normalized(
    response=MARKET,
    *,
    method="get_stock_data",
    args=None,
    provider="yfinance",
    received=NOW,
    analysis_date="2024-01-02",
    outcome="success",
):
    return normalize_observation(
        method,
        provider,
        args or ("NVDA", "2023-12-01", analysis_date),
        {},
        response,
        outcome,
        "ReadTimeout" if outcome == "error" else None,
        symbol="NVDA",
        analysis_date=analysis_date,
        received_at=received,
    )


@pytest.fixture
def setup(tmp_path):
    database = initialize_database(tmp_path / "web.sqlite3")
    local = database.local_workspace()
    scope = {"workspace_id": local.workspace_id, "owner_id": local.user_id}
    service = ResearchService(database, tmp_path / "artifacts", clock=lambda: NOW)
    store = ResearchEvidenceStore(database, tmp_path / "artifacts", clock=lambda: NOW)
    run = service.create_run(
        {"ticker": "NVDA", "date": "2024-01-02", "analysts": ["market"]}, **scope
    )
    claim = service.tasks.claim_next("evidence-test", 90)
    manifest = service.tasks.manifest(claim)["content"]
    yield service, store, scope, run, claim, manifest
    database.dispose()


def test_price_requires_actual_finite_consistent_ohlcv_and_closed_date():
    details, body = normalized()
    assert details["status"] == "validated" and details["price_validated"] is True
    assert json.loads(body)["rows"][0]["close"] == 105
    assert details["data_as_of"] == "2024-01-02"


@pytest.mark.parametrize(
    "response",
    [
        "The model verified a strong Buy with price 105",
        MARKET.replace(",105,", ",nan,"),
        MARKET.replace("110,95", "90,95"),
        MARKET.replace("2024-01-02,", "2024-01-03,"),
        MARKET + "2024-01-02,100,110,95,105,1000\n",
        MARKET.replace("2024-01-02,", "2023-12-29,"),
        "not,csv\napi_key,private-secret",
        "",
    ],
)
def test_unverified_or_bad_price_does_not_qualify(response):
    details, _ = normalized(response)
    result = qualify([details], ["market"], capture_started=True)
    assert result["status"] == "insufficient"
    assert result["eligible_for_rating"] is False


@pytest.mark.parametrize(
    "received, valid",
    [
        (datetime(2024, 1, 2, 15, tzinfo=UTC), False),
        (datetime(2024, 1, 2, 20, 59, tzinfo=UTC), False),
        (datetime(2024, 1, 2, 21, tzinfo=UTC), True),
    ],
)
def test_intraday_rows_are_not_completed_prices(received, valid):
    assert normalized(received=received)[0]["price_validated"] is valid


def test_verified_snapshot_direct_tool_has_real_field_validation():
    snapshot = "- Latest trading row used: 2024-01-02\n" + "\n".join(
        f"| {key} | {value} |"
        for key, value in {
            "Open": 100,
            "High": 110,
            "Low": 95,
            "Close": 105,
            "Volume": 1000,
        }.items()
    )
    details, _ = normalized(
        snapshot, method="get_verified_market_snapshot", args=("NVDA", "2024-01-02", 30)
    )
    assert details["price_validated"] is True
    assert (
        normalized(
            snapshot.replace("105", "inf"),
            method="get_verified_market_snapshot",
            args=("NVDA", "2024-01-02", 30),
        )[0]["price_validated"]
        is False
    )


def test_financial_and_news_allowlists_drop_credentials_and_encoded_query_data():
    secret = "private/secret+marker"
    financial = {
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2023-12-31",
                "totalRevenue": "100",
                "netIncome": "20",
                "api_key": secret,
                "contact": "someone@example.com",
            }
        ],
        "headers": {"Authorization": secret},
    }
    details, body = normalized(
        financial,
        method="get_income_statement",
        args=("NVDA", "quarterly", "2024-01-02"),
        provider="alpha_vantage",
    )
    assert details["status"] == "validated"
    assert secret not in body and "someone@" not in body
    news = {
        "feed": [
            {
                "title": "Chip news api_key=private-secret email someone@example.com",
                "url": "https://news.example/article?token=private%2Fsecret%2Bmarker#secret",
                "time_published": "20240102T140000",
                "authors": [secret],
            }
        ]
    }
    details, body = normalized(news, method="get_news", provider="alpha_vantage")
    assert details["status"] == "validated"
    assert "private-secret" not in body and "private%" not in body and "someone@" not in body
    assert json.loads(body)["articles"][0]["url"] == "https://news.example/article"


def test_unknown_and_oversize_responses_never_save_raw_text():
    details, body = normalized(
        {"api_key": "private-secret-marker"}, method="get_fundamentals", args=("NVDA", "2024-01-02")
    )
    assert details["status"] == "unknown"
    assert not body or "private-secret" not in body
    details, body = normalized("private-secret-marker" * (MAX_RESPONSE_CHARS // 10))
    assert details["truncated"] is True and body is None


def test_social_capture_keeps_dates_but_drops_usernames_and_contact():
    details, body = normalized(
        "[2024-01-02T12:00:00Z · @private-user · Bullish] NVDA news someone@example.com token=private-marker\n",
        method="get_stocktwits_messages",
        provider="stocktwits",
    )
    assert details["category"] == "social" and details["status"] == "available"
    assert "private-user" not in body and "private-marker" not in body and "someone@" not in body


def test_quality_requires_all_selected_dimensions_and_never_validates_model_levels():
    market = normalized()[0]
    assert qualify([market], ["market"], capture_started=True)["status"] == "usable"
    missing = qualify([market], ["market", "fundamentals", "social"], capture_started=True)
    assert missing["status"] == "degraded" and not missing["eligible_for_rating"]
    assert missing["missing_categories"] == ["fundamentals", "social"]
    assert not missing["eligible_for_levels"] and not missing["eligible_for_sizing"]
    assert qualify([], ["market"], capture_started=False)["status"] == "unverified"


def test_capture_persists_private_allowlisted_body_and_scoped_paginated_api(setup):
    service, store, scope, run, claim, manifest = setup
    capture = store.begin(claim, manifest)
    with evidence_scope(capture):
        assert (
            observed_call(
                "get_stock_data",
                "yfinance",
                ("NVDA", "2023-12-01", "2024-01-02"),
                {"api_key": "private-secret"},
                lambda: MARKET,
            )
            == MARKET
        )
    page = store.list_evidence(run["id"], **scope, include_body=True)
    assert page.quality.status == "usable" and len(page.items) == 1
    assert "private-secret" not in page.model_dump_json()
    assert page.items[0].published_at is None
    assert page.items[0].data_as_of == "2024-01-02"
    files = list(
        (service.artifacts_root / scope["workspace_id"] / run["id"] / "evidence").glob("*.json")
    )
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
    with store.database.session() as session:
        snapshot = session.scalar(select(DataSnapshot))
        assert snapshot.details["db_as_of_kind"] == "capture_instant_not_source_time"
        assert "credential" not in json.dumps(snapshot.details)
    app = FastAPI()
    app.include_router(create_research_evidence_router(lambda: store, lambda: scope))
    with TestClient(app) as client:
        response = client.get(f"/api/v1/runs/{run['id']}/evidence?include_body=true")
        assert response.status_code == 200
        assert response.json()["quality"]["eligible_for_rating"] is True
        assert client.get(f"/api/v1/runs/{run['id']}/quality").json()["status"] == "usable"
        assert client.get(f"/api/v1/runs/{run['id']}/evidence?limit=101").status_code == 422


def test_failed_adapter_attempt_and_fallback_are_both_visible_without_upstream_error(setup):
    _service, store, scope, run, claim, manifest = setup
    capture = store.begin(claim, manifest)

    def bad():
        raise VendorUnavailableError(
            "https://vendor.test?api_key=private-secret; contact@example.com"
        )

    with evidence_scope(capture):
        with pytest.raises(VendorUnavailableError):
            observed_call(
                "get_stock_data", "alpha_vantage", ("NVDA", "2023-12-01", "2024-01-02"), {}, bad
            )
        observed_call(
            "get_stock_data", "yfinance", ("NVDA", "2023-12-01", "2024-01-02"), {}, lambda: MARKET
        )
    result = store.list_evidence(run["id"], **scope, include_body=True)
    assert {row.provider for row in result.items} == {"alpha_vantage", "yfinance"}
    assert {row.status for row in result.items} == {"error", "validated"}
    assert result.quality.status == "degraded"
    assert (
        "private-secret" not in result.model_dump_json()
        and "contact@" not in result.model_dump_json()
    )


def test_corrupt_artifact_revokes_qualification_and_missing_evidence_is_unverified(setup):
    service, store, scope, run, claim, manifest = setup
    assert store.get_quality(run["id"], **scope).status == "unverified"
    capture = store.begin(claim, manifest)
    with evidence_scope(capture):
        observed_call(
            "get_stock_data", "yfinance", ("NVDA", "2023-12-01", "2024-01-02"), {}, lambda: MARKET
        )
    files = list(service.artifacts_root.rglob("*.json"))
    files[0].write_text('{"body":"private-secret-marker"}')
    page = store.list_evidence(run["id"], **scope, include_body=True)
    assert page.quality.status == "insufficient" and page.items[0].body is None
    assert not page.quality.eligible_for_rating
    assert "private-secret-marker" not in page.model_dump_json()


def test_capture_failure_is_visible_and_worker_validator_cannot_treat_it_as_success(
    setup, monkeypatch
):
    _service, store, _scope, _run, claim, manifest = setup
    capture = store.begin(claim, manifest)

    def broken(*args):
        raise OSError("private-secret-marker")

    monkeypatch.setattr(store, "_write", broken)
    with evidence_scope(capture), pytest.raises(EvidenceCaptureError) as caught:
        observed_call(
            "get_stock_data", "yfinance", ("NVDA", "2023-12-01", "2024-01-02"), {}, lambda: MARKET
        )
    assert "private-secret" not in str(caught.value)
    with pytest.raises(ResearchError, match="证据捕获"):
        capture.verify()


def test_other_owner_and_cursor_are_rejected_before_reading_artifacts(setup):
    _service, store, scope, run, _claim, _manifest = setup
    with store.database.session() as session:
        owner, workspace = str(uuid4()), str(uuid4())
        session.add(User(id=owner, name="Other"))
        session.flush()
        session.add(Workspace(id=workspace, owner_id=owner, name="Other"))
        session.flush()
        session.add(
            Membership(id=str(uuid4()), workspace_id=workspace, user_id=owner, role="owner")
        )
    with pytest.raises(ResearchError) as caught:
        store.list_evidence(run["id"], workspace_id=workspace, owner_id=owner)
    assert caught.value.status_code == 404
    with pytest.raises(ResearchError) as caught:
        store.list_evidence(run["id"], **scope, cursor=str(uuid4()))
    assert caught.value.code == "invalid_evidence_cursor"


def test_contextvars_do_not_leak_across_concurrent_runs(setup):
    _service, store, _scope, _run, claim, manifest = setup
    capture = store.begin(claim, manifest)
    with evidence_scope(capture), ThreadPoolExecutor(max_workers=1) as pool:
        # Raw threads do not accidentally inherit private run context.
        assert (
            pool.submit(
                observed_call, "get_stock_data", "yfinance", (), {}, lambda: MARKET
            ).result()
            == MARKET
        )
    assert (
        store.get_quality(
            claim.run_id, workspace_id=claim.workspace_id, owner_id=claim.owner_id
        ).evidence_count
        == 0
    )


def test_route_and_direct_tool_hooks_capture_real_offline_adapters(setup, monkeypatch):
    from tradingagents.agents import tools
    from tradingagents.dataflows import router

    _service, store, scope, run, claim, manifest = setup
    capture = store.begin(claim, manifest)
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *args, **kwargs: MARKET
    )
    monkeypatch.setattr(router, "get_vendor", lambda *args: "yfinance")
    monkeypatch.setattr(
        tools, "build_verified_market_snapshot", lambda *args: "unknown direct shape"
    )
    with evidence_scope(capture):
        router.route_to_vendor("get_stock_data", "NVDA", "2023-12-01", "2024-01-02")
        tools.get_verified_market_snapshot.func("NVDA", "2024-01-02", 30, "2024-01-02")
    page = store.list_evidence(run["id"], **scope)
    assert {row.method for row in page.items} == {"get_stock_data", "get_verified_market_snapshot"}
    assert page.quality.status == "degraded"


def test_active_credentials_raw_and_encoded_variants_are_scrubbed_before_storage(setup):
    from urllib.parse import quote

    _service, store, scope, run, claim, manifest = setup
    capture = store.begin(claim, manifest)
    secret = "private/credential+marker%"
    news = {
        "feed": [
            {
                "title": f"News {secret} and {quote(secret, safe='')}",
                "url": "https://news.example/article",
                "time_published": "20240102T140000",
            }
        ]
    }
    with data_credentials_scope(lambda _: {"api_key": secret}), evidence_scope(capture):
        observed_call(
            "get_news", "alpha_vantage", ("NVDA", "2023-12-01", "2024-01-02"), {}, lambda: news
        )
    encoded = store.list_evidence(run["id"], **scope, include_body=True).model_dump_json()
    assert secret not in encoded and quote(secret, safe="") not in encoded
    assert "[redacted]" in encoded


def test_langgraph_parallel_nodes_receive_the_same_scoped_recorder(setup):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    _service, store, scope, run, claim, manifest = setup

    class State(TypedDict):
        left: str
        right: str

    def left(state):
        observed_call(
            "get_stock_data", "yfinance", ("NVDA", "2023-12-01", "2024-01-02"), {}, lambda: MARKET
        )
        return {"left": "done"}

    def right(state):
        observed_call(
            "get_news",
            "yfinance",
            ("NVDA", "2023-12-01", "2024-01-02"),
            {},
            lambda: "<unavailable>",
        )
        return {"right": "done"}

    graph = StateGraph(State)
    graph.add_node("l", left)
    graph.add_node("r", right)
    for name in ("l", "r"):
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    with evidence_scope(store.begin(claim, manifest)):
        graph.compile().invoke({"left": "", "right": ""})
    page = store.list_evidence(run["id"], **scope)
    assert len(page.items) == 2
    assert {row.method for row in page.items} == {"get_stock_data", "get_news"}


def test_unknown_source_cannot_become_qualified_price():
    details, _ = normalized(provider="unknown")
    assert details["status"] == "unknown" and details["price_validated"] is False


@pytest.mark.parametrize("bad_date", ["2024-01-02junk", "2023-12-31", "2024-01-01"])
def test_every_historical_row_requires_a_strict_date_and_actual_exchange_session(bad_date):
    response = MARKET.replace("2024-01-02,", f"{bad_date},")
    if bad_date != "2024-01-02junk":
        response += "2024-01-02,100,110,95,105,1000\n"
    details, _ = normalized(response)
    assert not details["price_validated"]
    assert not qualify([details], ["market"], capture_started=True)["eligible_for_rating"]


def test_legitimate_iso_daily_timestamp_is_accepted_without_prefix_truncation():
    assert normalized(MARKET.replace("2024-01-02,", "2024-01-02 00:00:00,"))[0]["price_validated"]
