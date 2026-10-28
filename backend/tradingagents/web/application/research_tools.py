"""Claim-bound financial tools; the external Agent never receives provider credentials."""

import json
from datetime import date, timedelta
from typing import Literal

from pydantic import Field, ValidationError

from tradingagents.web.api.base import ContractModel
from tradingagents.web.domain.tasks import ResearchError


class WindowInput(ContractModel):
    look_back_days: int = Field(default=90, ge=1, le=365, strict=True)


class IndicatorInput(WindowInput):
    indicator: Literal["close_50_sma", "close_200_sma", "close_10_ema", "rsi", "macd", "macds", "macdh", "boll", "boll_ub", "boll_lb", "atr", "vwma"]


class CompanyInput(ContractModel):
    section: Literal["overview", "income_statement", "balance_sheet", "cashflow", "insiders"]
    frequency: Literal["quarterly", "annual"] = "quarterly"


class NewsInput(ContractModel):
    look_back_days: int = Field(default=14, ge=1, le=30, strict=True)


class MacroInput(WindowInput):
    indicator: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")


class NoInput(ContractModel):
    pass


TOOL_INPUTS = {
    "research_prices": (WindowInput, "Read OHLCV history for this research's fixed stock and date. Values include source, coverage and evidence references; do not estimate missing prices."),
    "research_indicators": (IndicatorInput, "Read a technical indicator for this research's fixed stock and date."),
    "research_company": (CompanyInput, "Read company financials/disclosures or insiders for the fixed stock, limited to the analysis date."),
    "research_news": (NewsInput, "Read a source news sample for the fixed stock and date. A sample is not all-market sentiment."),
    "research_macro": (MacroInput, "Read a configured FRED macro series, respecting the analysis date and vintage availability."),
    "research_context": (NoInput, "Read only the additional research snapshots explicitly frozen into this run. No browsing history or other accounts."),
}


def tool_definitions():
    return [{"name": name, "description": description, "inputSchema": model.model_json_schema()}
        for name, (model, description) in TOOL_INPUTS.items()]


class ResearchToolGateway:
    def __init__(self, manifest, capture, *, active, before_call, emit, invoker=None):
        self.manifest, self.capture = manifest, capture
        self.active, self.before_call, self.emit = active, before_call, emit
        self.invoker = invoker
        self.request = manifest["request"]
        self.symbol, self.day = self.request["ticker"], self.request["date"]
        self.calls = 0

    def __call__(self, name, arguments):
        self.active()
        if name not in TOOL_INPUTS or not isinstance(arguments, dict):
            raise ResearchError("runtime_tool_forbidden", "本次研究未授权此工具", 403)
        try:
            inputs = TOOL_INPUTS[name][0].model_validate(arguments)
        except ValidationError:
            # Never reflect raw arguments; they are untrusted and may contain secrets.
            return json.dumps({"error": "invalid_tool_input", "message": "工具参数不符合范围；标的和分析日期由研究任务固定"}, ensure_ascii=False)
        if self.calls >= self.request.get("max_tool_calls", 40):
            raise ResearchError("runtime_tool_budget_exceeded", "研究工具调用已达到预算，未发出新的数据请求")
        self.calls += 1
        self.before_call()
        self.emit("tool_started", {"tool": name})
        if name == "research_context":
            result = {"symbol": self.symbol, "analysis_date": self.day,
                "contexts": self.manifest.get("selection", {}).get("context_inputs", []),
                "message": "这些已冻结的补充资料不替代本次工具取证。"}
        else:
            position = len(self.capture.observations)
            method, args = self._operation(name, inputs)
            if self.invoker is None:
                from tradingagents.dataflows.router import route_to_vendor

                invoker = route_to_vendor
            else:
                invoker = self.invoker
            try:
                invoker(method, *args)
            except ResearchError:
                raise
            except Exception:
                self.capture.verify()
                # Actual failed vendor attempts are already archived by evidence_scope.
                result = {"error": "source_unavailable", "message": "来源未提供可用资料，请检查数据连接；不要猜测缺失值。"}
            else:
                result = {"symbol": self.symbol, "analysis_date": self.day, "evidence": []}
            evidence = []
            for observation in self.capture.observations[position:]:
                details, body = observation["details"], observation["body"]
                evidence.append({"reference": f"snapshot:{observation['id']}",
                    "source": details["provider"], "method": details["method"],
                    "status": details["status"], "received_at": details.get("received_at"),
                    "requested_as_of": details.get("requested_as_of"),
                    "data_as_of": details.get("data_as_of"), "issues": details.get("issues", []),
                    "data": json.loads(body) if body else None})
            result["evidence"] = evidence
        self.active()
        self.emit("tool_completed", {"tool": name})
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
        # Responses are excerpts, never unbounded vendor text or exception messages.
        if len(encoded.encode()) > 160_000:
            return json.dumps({"error": "tool_response_too_large", "message": "来源摘录过大，请减小窗口；完整已捕获证据保留在报告证据页。"}, ensure_ascii=False)
        return encoded

    def _operation(self, name, inputs):
        if name == "research_prices":
            start = max(date(1970, 1, 1), date.fromisoformat(self.day) - timedelta(days=inputs.look_back_days))
            return "get_stock_data", (self.symbol, start.isoformat(), self.day)
        if name == "research_indicators":
            return "get_indicators", (self.symbol, inputs.indicator, self.day, inputs.look_back_days)
        if name == "research_company":
            if inputs.section == "overview":
                return "get_fundamentals", (self.symbol, self.day)
            if inputs.section == "insiders":
                return "get_insider_transactions", (self.symbol, self.day)
            return f"get_{inputs.section}", (self.symbol, inputs.frequency, self.day)
        if name == "research_news":
            start = max(date(1970, 1, 1), date.fromisoformat(self.day) - timedelta(days=inputs.look_back_days))
            return "get_news", (self.symbol, start.isoformat(), self.day)
        return "get_macro_indicators", (inputs.indicator, self.day, inputs.look_back_days)
