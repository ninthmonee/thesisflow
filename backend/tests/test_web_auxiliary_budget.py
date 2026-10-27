"""Direct Jev HTTP attempts share the run budget, including threads and retries."""

import threading
import time
from types import SimpleNamespace

import pytest

from tradingagents.agents import post_screen
from tradingagents.dataflows.execution_budget import execution_budget_scope
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.workers.research import WorkerCallbacks


def callbacks(budget, active=lambda: None):
    output = []
    service = SimpleNamespace(
        tasks=SimpleNamespace(publish=lambda *args, **kwargs: output.append(kwargs))
    )
    result = WorkerCallbacks(service, None, active, time.monotonic())
    result.budget = budget
    return result, output


def response(status=200, usage=None):
    payload = {
        "answers": {
            "about": {"type": "noul", "noul": 0.9},
            "stance": {"type": "choice", "choice": "bullish", "confidence": 0.8},
        }
    }
    if usage is not None:
        payload["usage"] = usage
    return SimpleNamespace(status_code=status, headers={}, json=lambda: payload)


def test_every_retry_reserves_a_slot_before_http(monkeypatch):
    calls = []
    monkeypatch.setattr(post_screen.time, "sleep", lambda *args: None)
    monkeypatch.setattr(
        post_screen.requests, "post", lambda *args, **kwargs: calls.append(1) or response(429)
    )
    observer, _ = callbacks({"max_llm_calls": 1})
    with execution_budget_scope(observer), pytest.raises(ResearchError, match="预算"):
        post_screen.system_one({}, post_screen.QUESTIONS, settings={"api_key": "test-only-key"})
    assert len(calls) == 1
    assert observer.stats()["llm_calls"] == observer.stats()["uncertain_calls"] == 1


def test_parallel_jev_posts_inherit_the_same_bounded_scope(monkeypatch):
    calls, lock = [], threading.Lock()

    def post(*args, **kwargs):
        with lock:
            calls.append(1)
        return response()

    monkeypatch.setattr(post_screen.requests, "post", post)
    observer, _ = callbacks({"max_llm_calls": 3})
    with execution_budget_scope(observer), pytest.raises(ResearchError, match="预算"):
        post_screen._ask_each("NVDA", ["post"] * 32, settings={"api_key": "test-only-key"})
    assert len(calls) <= 3
    assert observer.stats()["llm_calls"] == 3
    assert observer.stats()["uncertain_calls"] == 3


def test_known_auxiliary_usage_is_counted_without_response_body_or_credentials(monkeypatch):
    monkeypatch.setattr(
        post_screen.requests,
        "post",
        lambda *args, **kwargs: response(usage={"input_tokens": 7, "output_tokens": 3}),
    )
    observer, output = callbacks({"max_llm_calls": 2})
    with execution_budget_scope(observer):
        result = post_screen.system_one(
            {"post": "private-source-post"},
            post_screen.QUESTIONS,
            settings={"api_key": "test-only-secret"},
        )
    assert result["about"]["noul"] == 0.9
    assert observer.stats()["tokens_in"] == 7 and observer.stats()["tokens_out"] == 3
    assert (
        observer.stats()["usage_reported_calls"] == 1 and observer.stats()["uncertain_calls"] == 0
    )
    assert "test-only-secret" not in str(output) and "private-source-post" not in str(output)


def test_cli_without_a_web_scope_retains_existing_retry_behavior(monkeypatch):
    calls = []
    monkeypatch.setattr(post_screen.time, "sleep", lambda *args: None)
    monkeypatch.setattr(
        post_screen.requests,
        "post",
        lambda *args, **kwargs: calls.append(1) or response(429 if len(calls) == 1 else 200),
    )
    assert (
        post_screen.system_one({}, post_screen.QUESTIONS, settings={"api_key": "test-only-key"})[
            "about"
        ]["noul"]
        == 0.9
    )
    assert len(calls) == 2


def test_late_start_rechecks_tokens_under_the_same_reservation_lock():
    checked, release, results = threading.Event(), threading.Event(), []

    def active():
        observer.check_budget()
        if threading.current_thread().name == "late-start":
            checked.set()
            assert release.wait(2)

    observer, _ = callbacks({"max_llm_calls": 5, "max_tokens_total": 10}, active)
    observer.on_llm_start({}, [], run_id="already-running")

    def late():
        try:
            observer.on_llm_start({}, [], run_id="late")
            results.append("accepted")
        except ResearchError:
            results.append("blocked")

    thread = threading.Thread(target=late, name="late-start")
    thread.start()
    assert checked.wait(2)
    with pytest.raises(ResearchError, match="预算"):
        observer.on_llm_end(
            SimpleNamespace(llm_output={"usage": {"input_tokens": 5, "output_tokens": 5}}),
            run_id="already-running",
        )
    release.set()
    thread.join(2)
    assert results == ["blocked"] and observer.stats()["llm_calls"] == 1
