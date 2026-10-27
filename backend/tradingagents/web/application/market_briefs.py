"""On-demand, evidence-constrained market summaries with explicit model metadata.

The model selects and orders facts; all rendered text and numbers originate in
the frozen snapshot. This keeps an unsupported flow narrative out of a brief.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from pydantic import Field, ValidationError

from tradingagents.web.api.intelligence_schemas import (
    BriefClaim,
    BriefContent,
    BriefGeneration,
    IntelligenceError,
    MarketSnapshot,
)
from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.api.schemas import ContractModel
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.application.runtime import create_runtime_resolver
from tradingagents.web.domain.model_access import is_local_owner
from tradingagents.web.infrastructure.recovery_policy import requires_explicit_model_binding


class BriefSelection(ContractModel):
    state: str = Field(min_length=1, max_length=160)
    changes: list[str] = Field(default_factory=list, max_length=5)
    attention: list[str] = Field(default_factory=list, max_length=5)
    divergences: list[str] = Field(default_factory=list, max_length=5)
    risks: list[str] = Field(default_factory=list, max_length=5)


def _formatted(value: float | int | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def build_fact_candidates(snapshot: MarketSnapshot, language: str) -> dict[str, dict]:
    """Only server-computed, referencable observations become model choices."""
    english = language == "English"
    evidence = {row.id: row for row in snapshot.evidence}
    candidates = {}

    def add(identifier, section, text, references):
        references = list(dict.fromkeys(ref for ref in references if ref in evidence))
        if references:
            candidates[identifier] = {"id": identifier, "section": section, "text": text,
                                      "evidence_ids": references[:20]}

    available = [row.id for row in snapshot.evidence if row.value is not None and row.quality != "unavailable"]
    if not available:
        raise IntelligenceError("market_data_insufficient", "没有足够的可引用市场数值。")
    add("state:scope", "state",
        f"Observed window: {snapshot.window}, through {snapshot.as_of}. Conclusions cover completed trading days and the stated sample only."
        if english else f"当前观察窗口为 {snapshot.window}，截至 {snapshot.as_of}；结论仅覆盖已完成交易日与明确观察样本。",
        available[:3])
    for benchmark in snapshot.benchmarks:
        if benchmark.return_percent is not None:
            text = (f"{benchmark.symbol} price return over the window was {_formatted(benchmark.return_percent)}%; "
                    "ETF proxies and the volatility index have different meanings.") if english else (
                    f"{benchmark.symbol} 当前窗口价格变化为 {_formatted(benchmark.return_percent)}%；ETF 代理与波动指数含义分别解读。")
            add(f"change:{benchmark.symbol}", "changes", text, benchmark.evidence_ids)
        if benchmark.kind == "volatility_index" and benchmark.price is not None:
            add(f"state:{benchmark.symbol}", "state",
                f"{benchmark.symbol} was {_formatted(benchmark.price)}; it measures expected volatility, not a directional probability."
                if english else f"{benchmark.symbol} 水平为 {_formatted(benchmark.price)}，用于观察预期波动，不代表涨跌概率。",
                benchmark.evidence_ids)
    breadth = snapshot.breadth
    if breadth.available_count:
        text = (f"The fixed sample covered {breadth.available_count}/{breadth.requested_count} stocks. "
                "This is not full-market breadth.") if english else (
                f"固定观察样本覆盖 {breadth.available_count}/{breadth.requested_count} 只股票，不代表全市场广度。")
        if breadth.advancers is not None and breadth.decliners is not None:
            text += (f" Advancers {breadth.advancers}; decliners {breadth.decliners}.") if english else (
                f"上涨 {breadth.advancers} 只、下跌 {breadth.decliners} 只。")
        add("state:breadth", "state", text, breadth.evidence_ids)
    for sector in snapshot.sectors:
        if sector.relative_return_pp is None and sector.daily_rvol is None:
            continue
        if english:
            text = (f"{sector.symbol} relative return versus SPY was {_formatted(sector.relative_return_pp)} pp; "
                    f"daily RVOL {_formatted(sector.daily_rvol)}× with {sector.rvol_sample_count} reference sessions. "
                    "Volume activity describes attention, not verified fund flow.")
        else:
            text = (f"{sector.symbol} 相对 SPY 收益差为 {_formatted(sector.relative_return_pp)} 个百分点；"
                    f"日级 RVOL 为 {_formatted(sector.daily_rvol)}×，参考样本 {sector.rvol_sample_count} 日。"
                    "成交活跃仅表示交易关注，不是已验证的资金流。")
        add(f"attention:{sector.symbol}", "attention", text, sector.evidence_ids)
    for position, observation in enumerate(snapshot.observed_divergences):
        add(f"divergence:{position}", "divergences",
            "A precomputed divergence is present; compare the cited metrics and their coverage."
            if english else observation.text, observation.evidence_ids)
    add("risk:scope", "risks",
        "The breadth universe is a fixed sample; source coverage and delayed data limit interpretation."
        if english else "广度仅覆盖固定样本；来源覆盖与数据延迟会限制解释范围。", available[:1])
    add("risk:flows", "risks",
        "This snapshot does not establish ETF creations/redemptions or institutional transactions; volume is not a net-flow measure."
        if english else "本快照不能确认 ETF 净申赎或机构交易，成交量不能用作净流入指标。", available[:1])
    if snapshot.quality in {"partial", "stale"}:
        add("risk:quality", "risks",
            f"Snapshot quality is {snapshot.quality}; review missing or stale evidence before interpreting it."
            if english else "快照存在覆盖不足或过期数据，解释前需查看具体证据状态。", available[:1])
    return candidates


class MarketBriefGenerator:
    def __init__(self, model_service: ModelConfigurationService, *,
                 local_status_reader: Callable[[], Mapping[str, Any]] | None = None,
                 base_config: Mapping[str, Any] | None = None,
                 model_loader: Callable[[], tuple[Any, str, str]] | None = None):
        self.model_service = model_service
        self.local_status_reader = local_status_reader
        self.base_config = deepcopy(dict(base_config)) if base_config is not None else None
        self.model_loader = model_loader

    def _load_model(self):
        if self.model_loader is not None:
            return self.model_loader()
        template = self.model_service.settings()["default_template"]
        if template is not None:
            chosen = self.model_service.resolve_profile_snapshot(template["default_profile_id"], require_tested=True)
            snapshot = {"roles": {"quick": chosen}, "signature": hashlib.sha256(
                json.dumps(chosen, sort_keys=True).encode()).hexdigest()}
            manifest = {"workspace_id": self.model_service.workspace.workspace_id,
                        "owner_id": self.model_service.workspace.user_id, "runtime": {}, "request": {},
                        "selection": {"mode": "configured", "model_snapshot": snapshot}}
            config = create_runtime_resolver(self.model_service.database, self.base_config,
                                             self.model_service.secrets)(manifest)
            return config["__role_model_resolver"]("quick"), chosen["provider"], chosen["model"]
        if not is_local_owner(self.model_service.workspace.workspace_id, self.model_service.workspace.user_id):
            raise IntelligenceError("market_model_required", "请先为当前账号配置并检测市场简报模型；不会继承服务器账号。", 409)
        if self.local_status_reader is None or not self.local_status_reader().get("model_ready"):
            raise IntelligenceError("market_model_required", "请先配置市场简报模型或完成本机授权。")
        if requires_explicit_model_binding(self.model_service.database):
            raise IntelligenceError("recovery_credentials_rebind_required", "恢复后请先显式配置并检测简报模型，不继承本机账号。", 409)
        from tradingagents.llm_clients.factory import create_tier_client, tier_provider

        config = create_runtime_resolver(self.model_service.database, self.base_config)(
            {"workspace_id": self.model_service.workspace.workspace_id,
             "owner_id": self.model_service.workspace.user_id,
             "runtime": {}, "request": {}, "selection": {"mode": "local"}})
        return create_tier_client(config, "quick").get_llm(), tier_provider(config, "quick"), config["quick_think_llm"]

    def __call__(self, snapshot: dict, language: str = "Chinese") -> BriefGeneration:
        if language not in {"Chinese", "English"}:
            raise IntelligenceError("invalid_brief_language", "报告语言无效", 422)
        try:
            validated = MarketSnapshot.model_validate(snapshot)
            candidates = build_fact_candidates(validated, language)
            if len({row.id for row in validated.evidence}) != len(validated.evidence):
                raise IntelligenceError("invalid_market_evidence", "市场证据编号不唯一。", 422)
            model, provider, model_id = self._load_model()
            prompt = [
                {"role": "system", "content": (
                    "Select and rank the most informative supplied facts for a market brief. "
                    "Return only the BriefSelection JSON schema. Each field contains candidate IDs only. "
                    "The state ID must belong to section state, and each list must match its section. "
                    "Do not write prose, numbers, causal claims, additional IDs or external research. "
                    "Do not call external tools. The server renders exact original evidence and warnings. "
                    "Choose up to five changes/attention/divergences/risks, prioritizing meaningful differences."
                )},
                {"role": "user", "content": json.dumps({
                    "snapshot_id": validated.id, "as_of": validated.as_of, "language": language,
                    "candidates": list(candidates.values()), "output_schema": BriefSelection.model_json_schema(),
                }, ensure_ascii=False)},
            ]
            try:
                structured = model.with_structured_output(BriefSelection)
            except (AttributeError, NotImplementedError):
                structured = None
            if structured is not None:
                selected = BriefSelection.model_validate(structured.invoke(prompt))
            else:
                raw = model.invoke(prompt).content
                if not isinstance(raw, str) or len(raw) > 20000:
                    raise ValueError("invalid selection response")
                selected = BriefSelection.model_validate_json(raw)
            sections = {"state": [selected.state], "changes": selected.changes,
                        "attention": selected.attention, "divergences": selected.divergences,
                        "risks": selected.risks}
            resolved = {}
            known_ids = {row.id for row in validated.evidence}
            for section, identifiers in sections.items():
                claims = []
                for identifier in dict.fromkeys(identifiers):
                    candidate = candidates.get(identifier)
                    if candidate is None or candidate["section"] != section:
                        raise IntelligenceError("invalid_market_brief", "模型引用了候选范围之外的事实，简报未发布。", 422)
                    if any(ref not in known_ids for ref in candidate["evidence_ids"]):
                        raise IntelligenceError("invalid_brief_evidence", "简报引用了快照之外的证据。", 422)
                    claims.append(BriefClaim(text=candidate["text"], evidence_ids=candidate["evidence_ids"]))
                resolved[section] = claims[0] if section == "state" else claims
            return BriefGeneration(content=BriefContent(**resolved), provider=provider, model=model_id)
        except IntelligenceError:
            raise
        except ModelConfigurationError as exc:
            raise IntelligenceError(exc.code, exc.message, exc.status) from None
        except (ValidationError, ValueError, TypeError):
            raise IntelligenceError("invalid_market_brief", "模型输出或市场证据未通过结构校验，简报未发布。", 422) from None
        except Exception:
            raise IntelligenceError("market_brief_failed", "模型未能完成市场简报，请检查连接后重试。", 503) from None
