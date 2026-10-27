"""Foreground research execution for an independently supervised worker process."""

from __future__ import annotations

import argparse
import inspect
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from tradingagents.agents.rating import RATING_REVIEW, run_rating
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.credentials import data_credentials_scope
from tradingagents.dataflows.date_window import date_timezone_scope
from tradingagents.dataflows.evidence import EvidenceCaptureError, evidence_scope
from tradingagents.dataflows.execution_budget import execution_budget_scope
from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.api.research_data_schemas import ResearchDataConnectionError
from tradingagents.web.application.research import REPORT_LABELS, ResearchService
from tradingagents.web.application.research_evidence import ResearchEvidenceStore
from tradingagents.web.domain.tasks import (
    LeaseLost,
    ResearchError,
    RunCancelled,
    RunTimedOut,
    TaskClaim,
)
from tradingagents.web.infrastructure.runtime_contract import verify_graph_contract
from tradingagents.web.infrastructure.worker_health import WorkerHeartbeatStore

from .checkpoints import CheckpointFiles, file_hash


def safe_failure(exception: Exception) -> tuple[str, str]:
    if isinstance(exception, EvidenceCaptureError):
        return (
            "research_evidence_capture_failed",
            "工具证据捕获失败；已有模型报告保留，未发布有效结论",
        )
    if isinstance(exception, (ResearchError, ModelConfigurationError, ResearchDataConnectionError)):
        return exception.code, exception.message
    if isinstance(exception, TimeoutError) or type(exception).__name__ in {
        "APITimeoutError",
        "ReadTimeout",
    }:
        return "upstream_timeout", "模型或数据请求超时，请检查连接后重试"
    if type(exception).__name__ in {"AuthenticationError", "PermissionDeniedError"}:
        return "model_authentication_failed", "模型凭据或权限不可用，请检查对应连接"
    if type(exception).__name__ == "RateLimitError":
        return "upstream_rate_limited", "模型或数据服务限流，请稍后重试"
    return "research_failed", "研究未能完成，请检查模型与数据连接后重新研究"


class ResearchWorker:
    def __init__(
        self,
        service: ResearchService,
        config_resolver: Callable[[dict[str, Any]], Mapping[str, Any]],
        *,
        graph_factory=None,
        worker_id: str | None = None,
        lease_seconds: float = 90,
        heartbeat_interval: float | None = None,
        max_run_seconds: float = 3600,
    ):
        if not 1 <= lease_seconds <= 3600 or max_run_seconds <= 0:
            raise ValueError("invalid lease or execution timeout")
        self.service, self.config_resolver = service, config_resolver
        self.graph_factory = graph_factory
        self.worker_id = worker_id or f"research-worker-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval or min(10, lease_seconds / 3)
        if not 0 < self.heartbeat_interval < lease_seconds:
            raise ValueError("heartbeat_interval must be shorter than the lease")
        self.max_run_seconds = max_run_seconds
        self.checkpoints = CheckpointFiles(service)
        self.evidence = ResearchEvidenceStore(
            service.database, service.artifacts_root, clock=service.tasks.clock
        )
        self.worker_health = WorkerHeartbeatStore(service.database, clock=service.tasks.clock)
        self._health_token = None

    def _runtime(
        self, claim: TaskClaim, manifest: dict, callbacks: list | None = None
    ) -> tuple[dict, dict]:
        resolver_parameters = inspect.signature(self.config_resolver).parameters
        accepts_callbacks = "callbacks" in resolver_parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in resolver_parameters.values()
        )
        resolved = dict(
            self.config_resolver(deepcopy(manifest), callbacks=callbacks)
            if accepts_callbacks
            else self.config_resolver(deepcopy(manifest))
        )
        private = {key: resolved.pop(key) for key in list(resolved) if key.startswith("__")}
        config = deepcopy(resolved)
        config.update(deepcopy(manifest.get("runtime", {})))
        request = manifest["request"]
        folder = self.checkpoints.attempt_directory(claim)
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        config.update(
            output_language=request["language"],
            max_debate_rounds=request["depth"],
            max_risk_discuss_rounds=request["depth"],
            checkpoint_enabled=request["checkpoint"],
            results_dir=str(folder / "results"),
            data_cache_dir=str(folder / "cache"),
            memory_log_path=str(folder / "memory" / "trading_memory.md"),
            research_request={
                key: request[key]
                for key in ("objective", "horizon", "holding_state")
                if key in request
            },
        )
        context_inputs = manifest.get("selection", {}).get("context_inputs", [])
        config["research_context"] = (
            deepcopy(context_inputs) if isinstance(context_inputs, list) else []
        )
        options = {}
        if private.get("__role_model_resolver") is not None:
            options["role_model_resolver"] = private["__role_model_resolver"]
        if private.get("__role_model_signature") is not None:
            options["role_model_signature"] = private["__role_model_signature"]
        if private.get("__data_connection_resolver") is not None:
            options["__data_connection_resolver"] = private["__data_connection_resolver"]
        return config, options

    def run_once(self) -> bool:
        claim = self.service.tasks.claim_next(self.worker_id, self.lease_seconds)
        if claim is None:
            return False
        started = time.monotonic()
        stopped, lost, timed_out = threading.Event(), threading.Event(), threading.Event()
        data_validator = None
        evidence_session = None
        callback = None
        request = {}
        prior_stats = self.service.usage_totals(
            claim.run_id,
            workspace_id=claim.workspace_id,
            owner_id=claim.owner_id,
            exclude_attempt_id=claim.attempt_id,
        )

        def heartbeat():
            while not stopped.wait(min(self.heartbeat_interval, 10)):
                if time.monotonic() - started > self.max_run_seconds:
                    timed_out.set()
                    return
                try:
                    self.service.tasks.heartbeat(claim, self.lease_seconds)
                    if self._health_token is not None:
                        self.worker_health.heartbeat(
                            self.worker_id, self._health_token, run_id=claim.run_id, state="busy"
                        )
                except LeaseLost:
                    lost.set()
                    return
                except Exception:
                    # A transient storage error is not an excuse to bypass fencing.
                    lost.set()
                    return

        def active():
            if data_validator is not None:
                data_validator()
            if evidence_session is not None:
                evidence_session.verify()
            if lost.is_set():
                raise LeaseLost()
            if timed_out.is_set() or time.monotonic() - started > self.max_run_seconds:
                raise RunTimedOut()
            if time.monotonic() - started + prior_stats.get("elapsed_seconds", 0) >= request.get(
                "max_runtime_seconds", 1800
            ):
                raise ResearchError(
                    "research_budget_exceeded",
                    "研究已达到累计执行时间预算；保留已有产出，重新研究可选择新预算",
                )
            if callback is not None:
                callback.check_budget()
            if self.service.tasks.active_status(claim) == "cancelling":
                raise RunCancelled()

        # This thread only renews a process-owned lease, never executes queued jobs.
        pulse = threading.Thread(target=heartbeat, name=f"lease-{claim.attempt_id}", daemon=True)
        pulse.start()
        callback = WorkerCallbacks(self.service, claim, active, started, prior_stats=prior_stats)
        graph = None
        checkpoint_context = None

        def capture_checkpoint():
            if checkpoint_context is None:
                return
            self.service.tasks.active_status(claim)
            manifest_hash, ticker, signature, thread_id = checkpoint_context
            reference = self.checkpoints.publish(claim, manifest_hash, ticker, signature, thread_id)
            if reference:
                self.service.tasks.publish(claim, checkpoint_ref=reference)
                self.checkpoints.prune_previous(claim, reference)

        try:
            manifest_record = self.service.tasks.manifest(claim)
            manifest, manifest_hash = manifest_record["content"], manifest_record["content_hash"]
            verify_graph_contract(manifest)
            request = manifest["request"]
            callback.budget = request
            evidence_session = self.evidence.begin(claim, manifest)
            if self._health_token is not None:
                self.worker_health.heartbeat(
                    self.worker_id, self._health_token, run_id=claim.run_id, state="busy"
                )
            config, options = self._runtime(claim, manifest, callbacks=[callback])
            data_resolver = options.pop("__data_connection_resolver", None)
            data_validator = getattr(data_resolver, "verify", None)
            factory = self.graph_factory
            if factory is None:
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                factory = TradingAgentsGraph
            parameters = inspect.signature(factory).parameters
            if not any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            ):
                options = {key: value for key, value in options.items() if key in parameters}
            with (
                date_timezone_scope("America/New_York"),
                execution_budget_scope(callback),
                evidence_scope(evidence_session),
                data_credentials_scope(data_resolver),
                run_config(config),
            ):
                active()
                graph = factory(request["analysts"], config=config, callbacks=[callback], **options)
                signature_method = getattr(graph, "_run_signature", None)
                signature = (
                    signature_method(manifest["asset_type"]) if callable(signature_method) else ""
                )
                if claim.checkpoint_ref and not signature:
                    raise ResearchError(
                        "checkpoint_unsupported", "当前执行图不支持完整检查点恢复，请重新研究"
                    )
                metadata = self.checkpoints.restore(
                    claim, manifest_hash, request["ticker"], signature
                )
                initial = graph.create_run_state(
                    request["ticker"], request["date"], manifest["asset_type"]
                )
                final_state = dict(initial)
                arguments = graph.propagator.get_graph_args(callbacks=[callback])
                thread_id = graph.begin_checkpoint(
                    request["ticker"], request["date"], manifest["asset_type"]
                )
                if thread_id is not None:
                    arguments.setdefault("config", {}).setdefault("configurable", {})[
                        "thread_id"
                    ] = thread_id
                if signature and thread_id:
                    checkpoint_context = (manifest_hash, request["ticker"], signature, thread_id)
                if metadata is not None:
                    if thread_id != metadata["thread_id"] or not getattr(graph, "_resuming", False):
                        raise ResearchError(
                            "checkpoint_incompatible", "执行图未接受完整检查点，请重新研究"
                        )
                    state = graph.graph.get_state(arguments.get("config", {}))
                    if not isinstance(state.values, dict):
                        raise ResearchError("checkpoint_invalid", "检查点缺少可恢复的图状态")
                    final_state.update(state.values)
                self.service.tasks.publish(claim, stage="analysts")
                for _messages, chunk in graph.stream_run(
                    graph.checkpoint_input(initial), **arguments
                ):
                    active()
                    if not isinstance(chunk, dict):
                        continue
                    final_state.update(chunk)
                    callback.publish_reports(final_state)
                    capture_checkpoint()
                active()
                if not final_state.get("final_trade_decision"):
                    raise ResearchError("decision_missing", "执行图结束时缺少最终决策，研究未完成")
                callback.publish_reports(final_state)
                quality = self.evidence.finalize(claim)
                export_state = deepcopy(final_state)
                if not quality.eligible_for_rating:
                    export_state["final_rating"] = RATING_REVIEW
                    notice = "**数据资格未通过：以下为保留的原始模型输出，评级、价格目标和仓位均未经有效结论资格确认。**\n\n"
                    for key in ("trader_investment_plan", "final_trade_decision"):
                        if export_state.get(key):
                            export_state[key] = notice + export_state[key]
                output = self.checkpoints.attempt_directory(claim) / "report"
                graph.save_reports(export_state, request["ticker"], save_path=output, html=True)
                for kind, filename in (
                    ("html", "complete_report.html"),
                    ("markdown", "complete_report.md"),
                ):
                    path = output / filename
                    if path.is_file() and not path.is_symlink():
                        path.chmod(0o600)
                        self.service.tasks.register_export(
                            claim,
                            f"_export_{kind}",
                            str(path.relative_to(self.service.artifacts_root)),
                            file_hash(path),
                        )
                active()
                memory_state = {
                    **final_state,
                    "final_rating": run_rating(final_state)
                    if quality.eligible_for_rating
                    else RATING_REVIEW,
                }
                graph.record_decision(request["ticker"], request["date"], memory_state)
                self.service.tasks.finish(claim, "completed", stats=callback.stats())
                with suppress(Exception):
                    graph.clear_checkpoint_on_success(
                        request["ticker"], request["date"], manifest["asset_type"]
                    )
        except LeaseLost:
            # A newer worker or the reaper owns the task; never overwrite it.
            pass
        except RunCancelled:
            with suppress(Exception):
                capture_checkpoint()
            with suppress(LeaseLost):
                self.service.tasks.finish(claim, "cancelled", stats=callback.stats())
        except Exception as exception:
            with suppress(Exception):
                capture_checkpoint()
            code, message = safe_failure(exception)
            with suppress(LeaseLost):
                self.service.tasks.finish(
                    claim,
                    "failed",
                    error_code=code,
                    error_message=message,
                    stats=callback.stats(),
                )
        finally:
            stopped.set()
            pulse.join(timeout=max(1, self.heartbeat_interval + 0.5))
            if graph is not None:
                with suppress(Exception):
                    graph.end_checkpoint()
            if self._health_token is not None:
                with suppress(Exception):
                    self.worker_health.heartbeat(self.worker_id, self._health_token)
        return True

    def run_forever(
        self, *, poll_seconds: float = 1, stop_event: threading.Event | None = None
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        stop = stop_event or threading.Event()
        self._health_token = self.worker_health.register(self.worker_id)
        try:
            while not stop.is_set():
                self.worker_health.heartbeat(self.worker_id, self._health_token)
                if not self.run_once():
                    stop.wait(min(poll_seconds, 10))
        finally:
            self.worker_health.stop(self.worker_id, self._health_token)
            self._health_token = None


class WorkerCallbacks(BaseCallbackHandler):
    """Publish counters and allowlisted reports, never raw prompts or errors."""

    raise_error = True

    def __init__(
        self,
        service: ResearchService,
        claim: TaskClaim,
        active: Callable[[], None],
        started: float,
        *,
        prior_stats=None,
    ):
        self.service, self.claim, self.active, self.started = service, claim, active, started
        self.lock = threading.RLock()
        self.counts = {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}
        self.seen: set[str] = set()
        self.finished: set[str] = set()
        self.prior = prior_stats or {}
        self.budget = {}
        self.usage_reported_calls = 0
        self.budget_exceeded = False

    def check_budget(self):
        with self.lock:
            limit = self.budget.get("max_tokens_total")
            total = sum(
                self.counts[key] + self.prior.get(key, 0) for key in ("tokens_in", "tokens_out")
            )
            if self.budget_exceeded or (limit is not None and total >= limit):
                self.budget_exceeded = True
                raise ResearchError(
                    "research_budget_exceeded",
                    "研究已达到累计模型调用或token预算；已发起调用的消耗仍保留，重新研究可选择新预算",
                )

    def reserve_model_call(self):
        identifier = str(uuid4())
        self.on_llm_start({}, [], run_id=identifier)
        return identifier

    def finish_model_call(self, reservation, usage=None):
        self.on_llm_end(SimpleNamespace(llm_output={"usage": usage or {}}), run_id=reservation)

    def stats(self) -> dict[str, Any]:
        with self.lock:
            return {
                **self.counts,
                "usage_reported_calls": self.usage_reported_calls,
                "uncertain_calls": max(0, self.counts["llm_calls"] - self.usage_reported_calls),
                "usage_quality": "complete"
                if self.counts["llm_calls"] > 0
                and self.usage_reported_calls == self.counts["llm_calls"]
                else "partial"
                if self.usage_reported_calls
                else "unknown",
                "elapsed_seconds": max(0, round(time.monotonic() - self.started, 1)),
            }

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.on_llm_start(serialized, [], **kwargs)

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.active()
        node = (kwargs.get("metadata") or {}).get("langgraph_node")
        stage = {
            "Bull Researcher": "research",
            "Bear Researcher": "research",
            "Research Manager": "research",
            "Trader": "trading",
            "Aggressive Analyst": "risk",
            "Conservative Analyst": "risk",
            "Neutral Analyst": "risk",
            "Portfolio Manager": "portfolio",
        }.get(node)
        with self.lock:
            identifier = str(kwargs.get("run_id") or uuid4())
            if identifier not in self.seen:
                self.check_budget()
                if self.counts["llm_calls"] + self.prior.get("llm_calls", 0) >= self.budget.get(
                    "max_llm_calls", 80
                ):
                    self.budget_exceeded = True
                    raise ResearchError(
                        "research_budget_exceeded", "研究已达到累计模型调用预算，未发起新的调用"
                    )
                self.seen.add(identifier)
                self.counts["llm_calls"] += 1
                self.service.tasks.publish(self.claim, stage=stage, stats=self.stats())

    def on_llm_end(self, response, **kwargs):
        usage = None
        with suppress(AttributeError, IndexError, TypeError):
            usage = response.generations[0][0].message.usage_metadata
        if not usage:
            output = getattr(response, "llm_output", None) or {}
            usage = (
                (output.get("token_usage") or output.get("usage") or {})
                if isinstance(output, Mapping)
                else {}
            )
        if not isinstance(usage, Mapping):
            usage = {}
        with self.lock:
            identifier = str(kwargs.get("run_id") or next(iter(self.seen - self.finished), uuid4()))
            if identifier in self.finished:
                return
            self.finished.add(identifier)
            known = 0
            for destination, names in (
                ("tokens_in", ("input_tokens", "prompt_tokens")),
                ("tokens_out", ("output_tokens", "completion_tokens")),
            ):
                with suppress(TypeError, ValueError, OverflowError):
                    value = usage.get(names[0], usage.get(names[1]))
                    if value is not None and not isinstance(value, bool) and int(value) >= 0:
                        self.counts[destination] += int(value)
                        known += 1
            if known == 2 and self.usage_reported_calls < self.counts["llm_calls"]:
                self.usage_reported_calls += 1
            self.service.tasks.publish(self.claim, stats=self.stats())
        self.active()

    def on_llm_error(self, error, **kwargs):
        # Do not log/persist provider exceptions; a started call without usage stays uncertain.
        self.service.tasks.publish(self.claim, stats=self.stats())
        self.active()

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.active()
        with self.lock:
            self.counts["tool_calls"] += 1
            self.service.tasks.publish(self.claim, stats=self.stats())

    def publish_reports(self, state: dict[str, Any]) -> None:
        reports = {
            key: state[key]
            for key in REPORT_LABELS
            if isinstance(state.get(key), str) and state[key]
        }
        for group, keys in (
            ("investment_debate_state", ("bull_history", "bear_history")),
            (
                "risk_debate_state",
                ("aggressive_history", "conservative_history", "neutral_history"),
            ),
        ):
            nested = state.get(group)
            if isinstance(nested, dict):
                reports.update(
                    {
                        key: nested[key]
                        for key in keys
                        if isinstance(nested.get(key), str) and nested[key]
                    }
                )
        stage = None
        if state.get("final_trade_decision"):
            stage = "portfolio"
        elif state.get("risk_debate_state", {}).get("history") or state.get(
            "trader_investment_plan"
        ):
            stage = "risk"
        elif state.get("investment_plan"):
            stage = "trading"
        elif state.get("investment_debate_state", {}).get("history"):
            stage = "research"
        self.service.tasks.publish(
            self.claim,
            stage=stage,
            reports=reports,
            rating=run_rating(state) if state.get("final_trade_decision") else None,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the independent TradingAgents research worker"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1)
    arguments = parser.parse_args()
    from tradingagents.web.application.runtime import create_runtime_resolver
    from tradingagents.web.infrastructure.database import initialize_database

    database = initialize_database(arguments.database)
    try:
        service = ResearchService(database, arguments.artifacts)
        worker = ResearchWorker(service, create_runtime_resolver(database))
        if arguments.once:
            worker.run_once()
        else:
            worker.run_forever(poll_seconds=arguments.poll_seconds)
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
