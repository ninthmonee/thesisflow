"""The existing twelve-role graph lifecycle, kept separate from local Agent runtimes."""

import inspect
from contextlib import suppress
from copy import deepcopy

from tradingagents.agents.rating import RATING_REVIEW, run_rating
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.workers.checkpoints import file_hash


class BuiltinWorkflowExecutor:
    def __init__(self, worker):
        self.worker, self.graph, self.checkpoint_context = worker, None, None
        self.claim = None

    def run(self, context):
        worker, claim, manifest = self.worker, context.claim, context.manifest
        self.claim = claim
        manifest_hash, config, options = context.manifest_hash, context.config, context.options
        request, callback, active = manifest["request"], context.callback, context.active
        factory = worker.graph_factory
        if factory is None:
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            factory = TradingAgentsGraph
        parameters = inspect.signature(factory).parameters
        if not any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            options = {key: value for key, value in options.items() if key in parameters}
        graph = self.graph = factory(request["analysts"], config=config, callbacks=[callback], **options)
        signature_method = getattr(graph, "_run_signature", None)
        signature = (
            signature_method(manifest["asset_type"]) if callable(signature_method) else ""
        )
        if claim.checkpoint_ref and not signature:
            raise ResearchError(
                "checkpoint_unsupported", "当前执行图不支持完整检查点恢复，请重新研究"
            )
        metadata = worker.checkpoints.restore(
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
            self.checkpoint_context = (manifest_hash, request["ticker"], signature, thread_id)
        if metadata is not None:
            if thread_id != metadata["thread_id"] or not getattr(graph, "_resuming", False):
                raise ResearchError(
                    "checkpoint_incompatible", "执行图未接受完整检查点，请重新研究"
                )
            state = graph.graph.get_state(arguments.get("config", {}))
            if not isinstance(state.values, dict):
                raise ResearchError("checkpoint_invalid", "检查点缺少可恢复的图状态")
            final_state.update(state.values)
        worker.service.tasks.publish(claim, stage="analysts")
        for _messages, chunk in graph.stream_run(
            graph.checkpoint_input(initial), **arguments
        ):
            active()
            if not isinstance(chunk, dict):
                continue
            final_state.update(chunk)
            callback.publish_reports(final_state)
            self.capture_checkpoint()
        active()
        if not final_state.get("final_trade_decision"):
            raise ResearchError("decision_missing", "执行图结束时缺少最终决策，研究未完成")
        callback.publish_reports(final_state)
        quality = worker.evidence.finalize(claim)
        export_state = deepcopy(final_state)
        if not quality.eligible_for_rating:
            export_state["final_rating"] = RATING_REVIEW
            notice = "**数据资格未通过：以下为保留的原始模型输出，评级、价格目标和仓位均未经有效结论资格确认。**\n\n"
            for key in ("trader_investment_plan", "final_trade_decision"):
                if export_state.get(key):
                    export_state[key] = notice + export_state[key]
        output = worker.checkpoints.attempt_directory(claim) / "report"
        graph.save_reports(export_state, request["ticker"], save_path=output, html=True)
        for kind, filename in (
            ("html", "complete_report.html"),
            ("markdown", "complete_report.md"),
        ):
            path = output / filename
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
                worker.service.tasks.register_export(
                    claim,
                    f"_export_{kind}",
                    str(path.relative_to(worker.service.artifacts_root)),
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
        worker.service.tasks.finish(claim, "completed", stats=callback.stats())
        with suppress(Exception):
            graph.clear_checkpoint_on_success(
                request["ticker"], request["date"], manifest["asset_type"]
            )

    def capture_checkpoint(self):
        if self.checkpoint_context is None:
            return
        self.worker.service.tasks.active_status(self.claim)
        manifest_hash, ticker, signature, thread_id = self.checkpoint_context
        reference = self.worker.checkpoints.publish(self.claim, manifest_hash, ticker, signature, thread_id)
        if reference:
            self.worker.service.tasks.publish(self.claim, checkpoint_ref=reference)
            self.worker.checkpoints.prune_previous(self.claim, reference)

    def close(self):
        if self.graph is not None:
            self.graph.end_checkpoint()
