"""An already open SSE stream stops before publishing new data after logout."""

import asyncio

import pytest

from tests.test_web_multiuser_isolation import DAY, site as site
from tradingagents.web.application.authentication import SESSION_COOKIE

AFTER_LOGOUT = "PRIVATE_REPORT_GENERATED_AFTER_SESSION_REVOCATION"


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
def test_logout_closes_existing_stream_before_new_research_data(site, prefix):
    user, service = site.owner, site.app.state.research_service
    run = service.create_run({"ticker": "NVDA", "date": DAY}, workspace_id=user.workspace_id,
        owner_id=user.user_id)
    token = user.client.cookies.get(SESSION_COOKIE)
    messages, revoked = [], []

    async def read():
        received_request, waiting = False, asyncio.Event()

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await waiting.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and b"data:" in message.get("body", b"") and not revoked:
                # Revoke only after the first actual event reached the ASGI client.
                site.app.state.authentication_service.logout(token)
                claim = service.tasks.claim_next("offline-after-logout")
                assert claim.run_id == run["id"]
                service.tasks.publish(claim, reports={"market_report": AFTER_LOGOUT})
                service.tasks.finish(claim, "completed")
                revoked.append(True)

        path = f"{prefix}/runs/{run['id']}/events"
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "scheme": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 54321),
            "headers": [(b"host", b"127.0.0.1:8000"), (b"accept", b"text/event-stream"),
                (b"cookie", f"{SESSION_COOKIE}={token}".encode()),
                (b"x-workspace-id", user.workspace_id.encode())]}
        await asyncio.wait_for(site.app(scope, receive, send), timeout=10)

    asyncio.run(read())
    starts = [message for message in messages if message["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 200
    assert revoked == [True]
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    assert b"data:" in body and AFTER_LOGOUT.encode() not in body
    assert body.count(b"\ndata:") == 1
    assert user.client.get(f"{prefix}/runs/{run['id']}/events", headers=user.headers).status_code == 401
    # The new report exists in persistence; absence from SSE is an authorization result.
    assert service.get_run(run["id"], workspace_id=user.workspace_id,
        owner_id=user.user_id)["reports"]["market_report"] == AFTER_LOGOUT
