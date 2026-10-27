"""Alternative observations preserve scope, missingness, provenance and private credentials."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tradingagents.dataflows.vendors.market_alternatives import (
    AlpacaOptionsProvider,
    AlternativeVendorError,
    IntrinioFlowsProvider,
    MassiveProvider,
    OfficialHTTP,
    parse_issuer_xml,
    safe_page,
)
from tradingagents.web.api.alternative_router import create_alternative_router
from tradingagents.web.api.alternative_schemas import AlternativeDataError
from tradingagents.web.application.market_alternatives import AlternativeMarketService
from tradingagents.web.infrastructure.alternative_models import AlternativeDataConnection
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.models import Membership, User
from tradingagents.web.infrastructure.secrets import MemorySecretStore, SecretStoreError


class Source:
    def __init__(self):
        self.calls = []
        self.error = None
        self.issuer_rows = [
            {"date": "2026-10-21", "nav": 100, "shares_outstanding": 1000},
            {"date": "2026-10-22", "nav": 110, "shares_outstanding": 1100},
            {"date": "2026-10-23", "nav": 109, "shares_outstanding": 1050},
            {"date": "2026-10-24", "nav": 109, "shares_outstanding": 1050},
        ]
        self.option_data = {
            "expirations": ["2026-10-30"],
            "selected_expirations": ["2026-10-30"],
            "contracts": [
                {
                    "contractSymbol": "NVDA261030C00100000",
                    "expiration": "2026-10-30",
                    "contract_type": "call",
                    "strike": 100,
                    "bid": 0,
                    "ask": 2,
                    "volume": 10,
                    "openInterest": 100,
                    "impliedVolatility": 0.4,
                    "lastTradeDate": "2026-10-23T19:00:00Z",
                },
                {
                    "contractSymbol": "NVDA261030P00100000",
                    "expiration": "2026-10-30",
                    "contract_type": "put",
                    "strike": 100,
                    "volume": 5,
                    "openInterest": 200,
                    "impliedVolatility": float("nan"),
                },
            ],
            "truncated": False,
        }
        self.news_data = {"rows": [], "truncated": False}
        self.flow_data = {"rows": [], "truncated": False}

    def _record(self, kind, args, kwargs):
        self.calls.append((kind, args, kwargs))
        if self.error:
            raise self.error

    def history(self, *args, **kwargs):
        self._record("history", args, kwargs)
        return {"rows": deepcopy(self.issuer_rows), "raw_sha256": "fixture-hash"}

    def options(self, *args, **kwargs):
        self._record("options", args, kwargs)
        return deepcopy(self.option_data)

    def news(self, *args, **kwargs):
        self._record("news", args, kwargs)
        return deepcopy(self.news_data)

    def flows(self, *args, **kwargs):
        self._record("flows", args, kwargs)
        return deepcopy(self.flow_data)


@pytest.fixture
def context(tmp_path):
    database = initialize_database(tmp_path / "observations.sqlite3")
    local, source, secrets = database.local_workspace(), Source(), MemorySecretStore()
    now = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    service = AlternativeMarketService(
        database,
        local,
        tmp_path / "new-cache",
        secret_store=secrets,
        providers=dict.fromkeys(("yahoo", "ishares", "massive", "intrinio", "alpaca"), source),
        clock=lambda: now[0],
    )
    yield service, database, local, source, secrets, now
    database.dispose()


def connect(service, provider="massive"):
    return service.create_connection(
        {
            "name": provider,
            "provider": provider,
            "api_key": "PRIVATE-KEY",
            **({"api_secret": "PRIVATE-SECRET"} if provider == "alpaca" else {}),
        }
    )


def test_no_key_free_sources_and_capabilities_do_not_make_requests(context):
    service, _db, _local, source, _secrets, _now = context
    capabilities = service.capabilities()
    assert source.calls == [] and capabilities.trading_enabled is False
    assert {row.id for row in capabilities.sources if row.configured} == {"yahoo", "ishares"}
    with pytest.raises(AlternativeDataError) as error:
        service.get_news_sentiment()
    assert error.value.code == "data_configuration_required"
    assert service.get_options("nvda").underlying == "NVDA"


def test_alternative_us_scope_rejects_foreign_or_other_assets_before_provider_call(context):
    service, _db, _local, source, _secrets, _now = context
    for symbol in ("ASML.AS", "VOD.L", "7203.T", "EURUSD=X", "ES=F", "BTC-USD", "ETH-EUR", "^GSPC"):
        for request in (
            lambda symbol=symbol: service.get_options(symbol),
            lambda symbol=symbol: service.get_etf_flows([symbol]),
            lambda symbol=symbol: service.get_news_sentiment(symbol=symbol),
        ):
            with pytest.raises(AlternativeDataError) as error:
                request()
            assert error.value.status_code == 422
    assert source.calls == []
    for symbol in ("BRK.B", "BRK-B", "TSM", "BNTX"):
        assert service.get_options(symbol).underlying == symbol
    assert len(source.calls) == 4


def test_issuer_estimates_use_previous_nav_not_trading_volume_and_filter_weekends(context):
    service, _db, _local, source, _secrets, _now = context
    points = service.get_etf_flows(["SOXX"]).funds[0].points
    assert [point.date for point in points] == ["2026-10-21", "2026-10-22", "2026-10-23"]
    assert points[0].net_flow_usd is None
    assert points[1].net_flow_usd == 10_000
    assert points[2].net_flow_usd == -5500
    assert all(point.flow_kind == "estimated" for point in points)
    assert service.get_etf_flows(["SOXX"]).funds[0].meta.cache == "hit"
    assert len(source.calls) == 1


def test_issuer_extreme_share_changes_require_action_and_gaps_are_not_daily_flows(context):
    service, _db, _local, source, _secrets, _now = context
    source.issuer_rows[1]["shares_outstanding"] = 3000
    result = service.get_etf_flows(["SOXX"]).funds[0]
    assert result.meta.quality == "action_required"
    assert result.points[1].quality == "action_required"
    assert result.points[1].net_flow_usd is None
    source.issuer_rows = [source.issuer_rows[0], source.issuer_rows[2]]
    result = service.get_etf_flows(["IVV"]).funds[0]
    assert result.points[1].quality == "partial" and result.points[1].net_flow_usd is None


@pytest.mark.parametrize(
    ("hour", "minute", "last_day"),
    [(1, 0, "2026-10-22"), (12, 0, "2026-10-22"), (16, 0, "2026-10-22"), (20, 1, "2026-10-23")],
)
def test_issuer_uses_only_closed_sessions_before_open_during_trading_and_after_close(
    context, hour, minute, last_day
):
    service, _db, _local, _source, _secrets, now = context
    now[0] = datetime(2026, 10, 23, hour, minute, tzinfo=UTC)
    fund = service.get_etf_flows(["SOXX"]).funds[0]
    assert fund.meta.as_of == fund.points[-1].as_of_date == last_day
    assert fund.meta.coverage["last_closed_session"] == last_day
    assert all(point.date <= last_day for point in fund.points)


@pytest.mark.parametrize(
    ("hour", "minute", "last_day"), [(17, 59, "2026-11-25"), (18, 1, "2026-11-27")]
)
def test_issuer_honors_exchange_early_close_and_holiday(context, hour, minute, last_day):
    service, _db, _local, source, _secrets, now = context
    source.issuer_rows = [
        {"date": "2026-11-25", "nav": 100, "shares_outstanding": 1000},
        {"date": "2026-11-26", "nav": 100, "shares_outstanding": 1000},
        {"date": "2026-11-27", "nav": 110, "shares_outstanding": 1050},
    ]
    now[0] = datetime(2026, 11, 27, hour, minute, tzinfo=UTC)
    fund = service.get_etf_flows(["SOXX"]).funds[0]
    assert fund.meta.as_of == last_day
    assert all(point.date != "2026-11-26" for point in fund.points)
    if hour >= 18:
        assert fund.points[-1].net_flow_usd == 5000


def test_yahoo_preserves_unknown_greeks_quote_times_and_missing_quantities(context):
    service, _db, _local, source, _secrets, _now = context
    result = service.get_options("NVDA")
    assert result.put_call_volume_ratio == 0.5 and result.put_call_oi_ratio == 2
    assert result.contracts[0].bid == 0
    assert result.contracts[0].last_trade_at is not None
    for item in result.contracts:
        assert item.quote_as_of is None and item.open_interest_as_of is None
        assert item.delta is None and item.multiplier is None
    assert result.contracts[1].implied_volatility is None
    source.option_data["contracts"][1]["volume"] = None
    assert service.get_options("AMD").put_call_volume_ratio is None
    assert result.meta.quality == "partial" and "本次返回合约" in result.ratio_scope


def test_signed_greeks_and_alpaca_indicative_provenance(context):
    service, _db, _local, source, _secrets, _now = context
    massive = connect(service)
    source.option_data = {
        "rows": [
            {
                "details": {
                    "ticker": "O:NVDA261030P00100000",
                    "expiration_date": "2026-10-30",
                    "contract_type": "put",
                    "strike_price": 100,
                    "shares_per_contract": 100,
                },
                "greeks": {"delta": -0.4, "theta": -0.2},
                "last_quote": {"last_updated": 1792782000000000000},
            }
        ],
        "expirations": ["2026-10-30"],
        "selected_expirations": ["2026-10-30"],
        "truncated": False,
    }
    item = service.get_options("NVDA", provider="massive", connection_id=massive.id).contracts[0]
    assert item.delta == -0.4 and item.theta == -0.2 and item.quote_as_of is not None
    massive_result = service.get_options("NVDA", provider="massive", connection_id=massive.id)
    assert massive_result.meta.quality == "partial"
    assert massive_result.meta.coverage["contracts_missing_iv"] == 1
    assert massive_result.meta.coverage["contracts_missing_quote_or_time"] == 1
    alpaca = connect(service, "alpaca")
    source.option_data["rows"] = {
        "NVDA261030P00100000": {"impliedVolatility": 0.4, "greeks": {"delta": -0.4}}
    }
    result = service.get_options("NVDA", provider="alpaca", connection_id=alpaca.id)
    assert result.meta.feed == "indicative" and "派生" in result.meta.delay_label
    assert result.put_call_volume_ratio is None and result.contracts[0].open_interest is None
    assert source.calls[-1][1][0:2] == ("PRIVATE-KEY", "PRIVATE-SECRET")


def test_news_is_ticker_sample_deduplicated_and_unknown_sentiment_is_not_neutral(context):
    service, _db, _local, source, _secrets, _now = context
    connection = connect(service)
    article = {
        "id": "a",
        "title": "Two companies",
        "article_url": "https://publisher.example/news",
        "publisher": {"name": "P"},
        "published_utc": "2026-10-25T11:00:00Z",
        "tickers": ["NVDA", "AMD"],
        "insights": [
            {"ticker": "NVDA", "sentiment": "positive"},
            {"ticker": "NVDA", "sentiment": "positive"},
            {"ticker": "AMD", "sentiment": "negative"},
        ],
    }
    source.news_data = {
        "rows": [
            article,
            article,
            {**article, "id": "b", "insights": [], "article_url": "javascript:bad"},
            {**article, "id": "old", "published_utc": "2026-10-01T11:00:00Z"},
        ],
        "truncated": True,
    }
    result = service.get_news_sentiment(connection_id=connection.id)
    assert result.article_count == 2 and result.publisher_count == 1 and result.ticker_count == 2
    assert (result.positive, result.negative, result.neutral, result.unclassified_articles) == (
        1,
        1,
        0,
        1,
    )
    assert result.sentiment_balance == 0 and result.articles[1].url is None
    assert result.meta.quality == "partial" and result.meta.coverage["truncated"] is True
    scoped = service.get_news_sentiment(symbol="NVDA", connection_id=connection.id)
    assert scoped.negative == 0 and scoped.sentiment_balance == 1


def test_intrinio_retains_file_date_effective_date_and_null_flow(context):
    service, _db, _local, source, _secrets, _now = context
    connection = connect(service, "intrinio")
    source.flow_data["rows"] = [
        {"date": "2026-10-23", "as_of_date": "2026-10-22", "fund_flow": -1500, "nav": 200},
        {"date": "2026-10-24", "fund_flow": None},
    ]
    result = service.get_etf_flows(
        ["SOXX"], provider="intrinio", connection_id=connection.id
    ).funds[0]
    assert result.points[0].date != result.points[0].as_of_date
    assert (
        result.points[0].net_flow_usd == -1500 and result.points[0].flow_kind == "vendor_reported"
    )
    assert result.points[1].net_flow_usd is None and result.points[1].as_of_date is None
    assert result.meta.quality == "partial"


def test_credentials_live_in_scoped_keyring_only_rotation_is_cas_and_cache_has_no_keys(
    context, tmp_path
):
    service, database, _local, _source, secrets, _now = context
    connection = connect(service)
    assert "PRIVATE" not in connection.model_dump_json()
    with database.session() as session:
        row = session.get(AlternativeDataConnection, connection.id)
        original_ref = row.credential_ref
        assert original_ref.startswith("data/") and "PRIVATE" not in original_ref
    service.get_news_sentiment(connection_id=connection.id)
    updated = service.update_connection(connection.id, {"revision": 1, "api_key": "ROTATED-KEY"})
    assert updated.revision == 2 and secrets.get(original_ref) is None
    with pytest.raises(AlternativeDataError) as error:
        service.update_connection(connection.id, {"revision": 1, "api_key": "BAD-STALE-KEY"})
    assert error.value.code == "revision_conflict"
    assert "BAD-STALE-KEY" not in secrets._values.values()
    for path in tmp_path.rglob("*.json"):
        assert "PRIVATE-KEY" not in path.read_text() and "ROTATED-KEY" not in path.read_text()
    with database.session() as session:
        assert "PRIVATE-KEY" not in json.dumps(
            [
                dict(row._mapping)
                for row in session.execute(select(AlternativeDataConnection.__table__))
            ],
            default=str,
        )


def test_owner_isolation_and_membership_revocation_apply_to_free_sources(context, tmp_path):
    service, database, local, source, secrets, now = context
    connection = connect(service)
    with database.session() as session:
        session.add(User(id="friend", name="Friend"))
        session.flush()
        session.add(
            Membership(
                id="friend-membership",
                workspace_id=local.workspace_id,
                user_id="friend",
                role="viewer",
            )
        )
    friend = AlternativeMarketService(
        database,
        replace(local, user_id="friend"),
        tmp_path / "friend-cache",
        secret_store=secrets,
        providers=service.providers,
        clock=lambda: now[0],
    )
    assert friend.list_connections() == []
    with pytest.raises(AlternativeDataError) as error:
        friend.get_news_sentiment(connection_id=connection.id)
    assert error.value.status_code == 404
    with pytest.raises(AlternativeDataError) as error:
        connect(friend)
    assert error.value.status_code == 403
    with database.session() as session:
        session.delete(session.get(Membership, "friend-membership"))
    with pytest.raises(AlternativeDataError) as error:
        friend.get_options("NVDA")
    assert error.value.status_code == 403 and source.calls == []


def test_transient_failure_can_use_stale_cache_but_auth_failure_cannot(context):
    service, _db, _local, source, _secrets, now = context
    service.get_options("NVDA")
    now[0] += timedelta(minutes=6)
    source.error = AlternativeVendorError("rate_limited", 429)
    stale = service.get_options("NVDA")
    assert stale.meta.cache == "stale" and stale.meta.quality == "stale"
    source.error = AlternativeVendorError("authentication_failed", 409)
    with pytest.raises(AlternativeDataError) as error:
        service.get_options("NVDA")
    assert error.value.code == "authentication_failed"


def test_connection_test_forces_network_and_does_not_accept_stale_success(context):
    service, _db, _local, source, _secrets, _now = context
    connection = connect(service)
    service.get_news_sentiment(connection_id=connection.id, limit=1)
    source.error = AlternativeVendorError("rate_limited", 429)
    tested = service.test_connection(connection.id, {"revision": 1, "capability": "news"})
    assert tested.success is False and tested.connection.test_results == {"news": "failed"}
    assert len(source.calls) == 2 and tested.connection.revision == 2


def test_keyring_failure_does_not_create_plaintext_fallback(context):
    service, database, _local, _source, _secrets, _now = context

    class Broken(MemorySecretStore):
        def set(self, reference, value):
            raise SecretStoreError("unsafe raw key")

    service.secrets = Broken()
    with pytest.raises(AlternativeDataError) as error:
        connect(service)
    assert error.value.code == "secret_store_unavailable" and "unsafe raw key" not in str(
        error.value
    )
    with database.session() as session:
        assert session.scalar(select(AlternativeDataConnection)) is None


def test_non_alpaca_rejects_secondary_secret_without_storing_it(context):
    service, _db, _local, _source, secrets, _now = context
    with pytest.raises(AlternativeDataError) as error:
        service.create_connection(
            {"name": "wrong", "provider": "massive", "api_key": "KEY", "api_secret": "WRONG-SECRET"}
        )
    assert error.value.status_code == 422 and secrets._values == {}
    connection = connect(service)
    references = dict(secrets._values)
    with pytest.raises(AlternativeDataError) as error:
        service.update_connection(connection.id, {"revision": 1, "api_secret": "WRONG-SECRET"})
    assert error.value.status_code == 422 and references == secrets._values


def test_router_config_required_and_invalid_request_are_explicit(context):
    service, _db, _local, source, _secrets, _now = context
    app = FastAPI()
    app.include_router(create_alternative_router(lambda: service))
    with TestClient(app) as client:
        response = client.get("/api/v1/market/news-sentiment")
        assert (
            response.status_code == 409
            and response.json()["error_code"] == "data_configuration_required"
        )
        assert client.get("/api/v1/market/options", params={"symbol": "NVDA"}).status_code == 200
        assert (
            client.get("/api/v1/market/options", params={"symbol": "http://bad"}).status_code == 422
        )
        assert (
            client.get("/api/v1/market/etf-flows", params={"lookback_days": 0}).status_code == 422
        )
    assert len(source.calls) == 1


def workbook(rows):
    header = (
        "<Row>"
        + "".join(
            f"<Cell><Data>{name}</Data></Cell>"
            for name in ("As Of", "NAV per Share", "Ex-Dividends", "Shares Outstanding")
        )
        + "</Row>"
    )
    body = "".join(
        f'<Row><Cell><Data>{day}</Data></Cell><Cell><Data>{nav}</Data></Cell><Cell ss:Index="4"><Data>{shares}</Data></Cell></Row>'
        for day, nav, shares in rows
    )
    return (
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet ss:Name="Disclaimers"><Table><Row><Cell><Data>Personal & noncommercial copyright retained</Data></Cell></Row></Table></Worksheet>'
        f'<Worksheet ss:Name="Historical"><Table>{header}{body}</Table></Worksheet></Workbook>'
    ).encode()


def test_issuer_xml_handles_official_sparse_columns_ampersands_and_rejects_entities_or_conflicts():
    rows = parse_issuer_xml(
        workbook([("Oct 22, 2026", "100", "1,000"), ("Oct 23, 2026", "N/A", "0")])
    )
    assert rows == [
        {"date": "2026-10-22", "nav": 100, "shares_outstanding": 1000},
        {"date": "2026-10-23", "nav": None, "shares_outstanding": None},
    ]
    for invalid in (
        b'<!DOCTYPE Workbook [<!ENTITY x SYSTEM "file:///etc/passwd">]><Workbook/>',
        workbook([("Oct 22, 2026", "100", "1000"), ("Oct 22, 2026", "101", "1000")]),
        b"<html>upstream format changed</html>",
    ):
        with pytest.raises(AlternativeVendorError) as error:
            parse_issuer_xml(invalid)
        assert error.value.code == "source_schema_changed"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.massive.com/v2/reference/news",
        "https://evil.example/v2/reference/news",
        "https://api.massive.com/v2/reference/news/other",
        "https://api.massive.com/v2/reference/news%2fother",
        "https://@api.massive.com/v2/reference/news",
        "https://api.massive.com:bad/v2/reference/news",
        None,
    ],
)
def test_pagination_rejects_other_hosts_paths_and_invalid_urls(url):
    with pytest.raises(AlternativeVendorError):
        safe_page(url, host="api.massive.com", path_prefix="/v2/reference/news")


class HTTP:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, deepcopy(params)))
        return self.responses.pop(0)


def test_official_paging_keeps_secrets_in_headers_and_preserves_truncation():
    http = HTTP(
        [
            {
                "results": [],
                "next_url": "https://api.massive.com/v2/reference/news?cursor=1&apiKey=LEAK",
            },
            {"results": [], "next_url": "https://api.massive.com/v2/reference/news?cursor=2"},
        ]
    )
    result = MassiveProvider(http=http).news("KEY", limit=1)
    assert result["truncated"] is True and len(http.calls) == 2
    assert http.calls[1][1] == {"Authorization": "Bearer KEY"} and "LEAK" not in http.calls[1][0]
    http = HTTP([{"fund_flows": [], "next_page": "token"}, {"fund_flows": [], "next_page": None}])
    IntrinioFlowsProvider(http=http).flows("KEY", "SOXX", since="2026-10-01", until="2026-10-25")
    assert http.calls[1][2]["next_page"] == "token" and "KEY" not in http.calls[0][0]
    http = HTTP([{"snapshots": {"NVDA261030C00100000": {}}, "next_page_token": None}])
    result = AlpacaOptionsProvider(http=http).options(
        "KEY", "SECRET", "NVDA", expiration="2026-10-30"
    )
    assert result["selected_expirations"] == ["2026-10-30"]
    assert (
        http.calls[0][2]["feed"] == "indicative"
        and http.calls[0][2]["expiration_date"] == "2026-10-30"
    )


def test_http_does_not_redirect_bounds_body_and_redacts_upstream_exceptions():
    class Response:
        status_code = 200

        def iter_content(self, size):
            yield b"123456"

        def close(self):
            self.closed = True

    class Session:
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            return response

    response = Response()
    with pytest.raises(AlternativeVendorError) as error:
        OfficialHTTP(Session()).request("https://api.massive.com/fixed", maximum=5)
    assert error.value.code == "source_response_too_large" and response.closed

    class Bad:
        def get(self, *args, **kwargs):
            raise RuntimeError("PRIVATE-SECRET")

    with pytest.raises(AlternativeVendorError) as error:
        OfficialHTTP(Bad()).request("https://api.massive.com/fixed")
    assert "PRIVATE-SECRET" not in str(error.value)
