"""Only explicit application frame documents opt in to same-origin embedding."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.web.infrastructure.secrets import MemorySecretStore
from tradingagents.web.server import create_app

RUN_ID = "11223344-5566-4788-99aa-bbccddeeff00"


@pytest.fixture
def framed_workbench(tmp_path):
    ui = tmp_path / "ui"
    (ui / "design").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>Workbench host</title>", encoding="utf-8")
    (ui / "design" / "index.html").write_text(
        "<!doctype html><title>Exact design frame</title>", encoding="utf-8"
    )
    (ui / "design" / "runtime.js").write_text("export const ready = true;", encoding="utf-8")
    config = deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path / "state")
    app = create_app(config, workbench_dir=ui, secret_store=MemorySecretStore())
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def assert_frame_policy(response, allowed):
    assert response.headers["x-frame-options"] == ("SAMEORIGIN" if allowed else "DENY")
    policy = response.headers["content-security-policy"]
    assert ("frame-ancestors 'self'" if allowed else "frame-ancestors 'none'") in policy
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "https:" not in policy and "*" not in policy


def test_exact_design_html_allows_same_origin_embedding(framed_workbench):
    response = framed_workbench.get("/workbench/design/index.html")
    assert response.status_code == 200
    assert "Exact design frame" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert_frame_policy(response, True)
    asset = framed_workbench.get("/workbench/design/runtime.js")
    assert asset.status_code == 200
    assert_frame_policy(asset, False)


@pytest.mark.parametrize(
    "path",
    [
        "/settings/models",
        "/settings/runtimes",
        "/settings/data",
        "/settings/sources",
        "/account",
        "/stocks",
        "/stocks/11223344-5566-4788-99aa-bbccddeeff00",
        "/market",
        "/sectors",
        "/themes?theme=semiconductors",
        "/insights/flows",
        "/insights/options",
        "/insights/news",
        "/research/new",
        f"/runs/{RUN_ID}",
        "/reports",
        "/investment",
    ],
)
def test_known_advanced_documents_explicitly_opt_in_to_embedding(framed_workbench, path):
    separator = "&" if "?" in path else "?"
    response = framed_workbench.get(f"/workbench/advanced{path}{separator}embedded=1")
    assert response.status_code == 200
    assert "Workbench host" in response.text
    assert_frame_policy(response, True)


@pytest.mark.parametrize(
    "path",
    [
        "/workbench/",
        "/workbench/login?embedded=1",
        "/workbench/register?embedded=1",
        "/workbench/advanced/login?embedded=1",
        "/workbench/advanced/settings/models",
        "/workbench/advanced/settings/models?embedded=0",
        "/workbench/advanced/settings/models?embedded=1&embedded=0",
        "/workbench/advanced/settings/models?embedded=1&embedded=1",
        "/workbench/advanced/settings/arbitrary?embedded=1",
        "/workbench/advanced/unknown?embedded=1",
        "/workbench/advanced/runs/not-a-run-id?embedded=1",
        "/workbench/advanced/stocks/../../login?embedded=1",
        "/workbench/design/%69ndex.html",
        "/workbench/advanced/settings/%6dodels?embedded=1",
        "/api/v1/auth/session?embedded=1",
        "/api/v1/bootstrap?embedded=1",
        "/api/v1/health?embedded=1",
    ],
)
def test_main_auth_api_and_unrecognized_routes_remain_unframeable(framed_workbench, path):
    response = framed_workbench.get(path)
    assert response.status_code in {200, 401}
    assert_frame_policy(response, False)


def test_design_document_cannot_be_loaded_from_an_external_frame(framed_workbench):
    response = framed_workbench.get(
        "/workbench/design/index.html",
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
    )
    assert response.status_code == 403
    assert "Exact design frame" not in response.text


def test_valid_long_unicode_question_reaches_auth_instead_of_body_limit(framed_workbench):
    body = json.dumps(
        {"ticker": "NVDA", "date": "2026-10-30", "research_instructions": "证" * 4000},
        ensure_ascii=False,
    ).encode()
    assert 8192 < len(body) < 32 * 1024
    response = framed_workbench.post(
        "/api/v1/runs",
        content=body,
        headers={"Origin": "http://127.0.0.1:8000", "Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_required"


@pytest.mark.parametrize("chunked", [False, True])
def test_research_create_still_rejects_more_than_32kb(framed_workbench, chunked):
    body = b"x" * (32 * 1024 + 1)
    response = framed_workbench.post(
        "/api/v1/runs",
        content=iter([body]) if chunked else body,
        headers={"Origin": "http://127.0.0.1:8000", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/runs"),
        ("POST", "/api/v1/runs/"),
        ("POST", f"/api/v1/runs/{RUN_ID}/cancel"),
        ("PUT", "/api/v1/runs"),
        ("POST", "/api/v1/auth/login"),
    ],
)
def test_question_body_allowance_does_not_expand_other_endpoints(framed_workbench, method, path):
    response = framed_workbench.request(
        method,
        path,
        content=b"x" * 8193,
        headers={"Origin": "http://127.0.0.1:8000", "Content-Type": "application/json"},
    )
    assert response.status_code == 413
