"""Personal investment policy and manual decision journal contracts."""

import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, field_validator, model_validator

from .base import ContractModel

Percent = Annotated[FiniteFloat, Field(ge=0, le=100, strict=True)]
Money = Annotated[FiniteFloat, Field(ge=0, le=1e15, strict=True)]
Price = Annotated[FiniteFloat, Field(ge=0.000001, le=1e12, strict=True)]
ScenarioPrice = Annotated[FiniteFloat, Field(ge=0, le=1e12, strict=True)]
Text = Annotated[str, Field(max_length=4000)]


class InvestmentRiskLimits(ContractModel):
    max_single_position_pct: Percent | None = None
    max_theme_exposure_pct: Percent | None = None
    min_cash_pct: Percent | None = None
    max_drawdown_pct: Percent | None = None
    max_loss_per_idea_pct: Percent | None = None
    minimum_margin_of_safety_pct: Percent | None = None
    min_reward_risk: Annotated[FiniteFloat, Field(gt=0, le=1000, strict=True)] | None = None


class InvestmentPrinciples(ContractModel):
    goal: Text = "长期财富增长；先活下来，再追求收益，让本金、收益与时间共同积累。"
    cycles: Text = "先研究宏观、行业与估值周期，再选资产；记录证据、阶段判断和相反信号。"
    quality: Text = "好赛道与好公司：核查持续增长、自由现金流、护城河、定价权、管理层及行业地位。"
    valuation: Text = "好公司也要好价格；分别判断质量、估值与安全边际，为错误留出空间。"
    sizing: Text = "分批建仓，控制单股和主题集中度，保留现金；先问判断错误会亏多少。"
    probability: Text = "用概率和情景代替确定预测；判断正确时多赚，错误时少亏，持续检验期望值。"
    discipline: Text = "选择适合自己的长期、左侧、趋势或波段方法；遵守买入、加仓、减仓、退出与复盘规则。"
    life: Text = "投资服务于生活；确定支出、生活现金、税务、流动性与资产分散优先于高波动投资。"


class InvestmentActionRules(ContractModel):
    buy: Text = "先完成周期、公司质量、估值和风险检查；满足自己的条件后分批建仓。"
    add: Text = "新证据支持原假设且价格与组合风险仍符合规则时，才考虑分批加仓。"
    reduce: Text = "估值过高、集中度超限、现金需求变化或证据减弱时，复核并考虑减仓。"
    exit: Text = "核心假设失效、风险越过本人边界或机会成本变化时，按预先记录的条件退出。"
    review: Text = "定期核对证据、估值、仓位与错误；分别记录判断质量、执行纪律与结果。"


class InvestmentPolicyWrite(ContractModel):
    name: str = Field(default="我的投资体系", min_length=1, max_length=120)
    enabled: bool = True
    objective: str = Field(default="长期可持续的财富增长", max_length=2000)
    style: Literal["long_term", "left_side", "trend", "swing"] = "long_term"
    principles: InvestmentPrinciples = Field(default_factory=InvestmentPrinciples)
    action_rules: InvestmentActionRules = Field(default_factory=InvestmentActionRules)
    risk_limits: InvestmentRiskLimits = Field(default_factory=InvestmentRiskLimits)
    review_interval_days: int | None = Field(default=None, ge=1, le=3650, strict=True)
    life_notes: Text = ""
    tax_notes: Text = ""

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("体系名称不能为空")
        return value.strip()


class InvestmentPolicyUpdate(InvestmentPolicyWrite):
    revision: int = Field(ge=0, strict=True)


class InvestmentPolicyView(InvestmentPolicyWrite):
    revision: int
    saved: bool
    updated_at: datetime | None = None
    scope_notice: str = "个人可编辑的投资纪律；空阈值表示尚未设置，不等于无限风险或已通过检查。"


class InvestmentScenario(ContractModel):
    target_price: ScenarioPrice | None = None
    probability_pct: Percent | None = None
    thesis: str = Field(default="", max_length=2000)


class InvestmentScenarios(ContractModel):
    bear: InvestmentScenario = Field(default_factory=InvestmentScenario)
    base: InvestmentScenario = Field(default_factory=InvestmentScenario)
    bull: InvestmentScenario = Field(default_factory=InvestmentScenario)

    @model_validator(mode="after")
    def coherent(self):
        values = [self.bear, self.base, self.bull]
        probabilities = [item.probability_pct for item in values]
        if sum(value for value in probabilities if value is not None) > 100 + 1e-6:
            raise ValueError("已填写的情景概率合计不能超过100%")
        if all(value is not None for value in probabilities) and abs(sum(probabilities) - 100) > 1e-6:
            raise ValueError("完整三情景概率必须合计100%；未估计的概率请留空")
        prices = [item.target_price for item in values]
        for i, value in enumerate(prices):
            if value is not None and any(other is not None and other < value for other in prices[i + 1:]):
                raise ValueError("情景价格须按悲观、基准、乐观从低到高排列")
        return self


class InvestmentRiskInputs(ContractModel):
    capital_total_usd: Money | None = None
    available_cash_usd: Money | None = None
    essential_cash_usd: Money | None = None
    current_position_pct: Percent | None = None
    target_position_pct: Percent | None = None
    current_theme_exposure_pct: Percent | None = None
    target_theme_exposure_pct: Percent | None = None
    current_drawdown_pct: Percent | None = None
    unlevered_long_only: bool = True

    @model_validator(mode="after")
    def coherent(self):
        total = self.capital_total_usd
        if total is not None and total <= 0:
            raise ValueError("资产总额必须大于0；未知时请留空")
        if total is not None and any(value is not None and value > total for value in (
            self.available_cash_usd, self.essential_cash_usd)):
            raise ValueError("现金和生活保留金不能超过资产总额")
        if (self.target_position_pct is not None and self.target_theme_exposure_pct is not None
                and self.target_position_pct > self.target_theme_exposure_pct):
            raise ValueError("目标主题仓位不能低于属于该主题的目标单股仓位")
        if (self.current_position_pct is not None and self.current_theme_exposure_pct is not None
                and self.current_position_pct > self.current_theme_exposure_pct):
            raise ValueError("当前主题仓位不能低于属于该主题的当前单股仓位")
        if total is not None and self.available_cash_usd is not None:
            cash_pct = self.available_cash_usd / total * 100
            if any(value is not None and value + cash_pct > 100 + 1e-6 for value in (
                self.current_position_pct, self.current_theme_exposure_pct)):
                raise ValueError("当前仓位与现金合计不能超过资产总额")
        return self


class InvestmentPlanWrite(ContractModel):
    ticker: str = Field(min_length=1, max_length=32)
    analysis_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    run_id: UUID | None = None
    title: str = Field(default="", max_length=160)
    theme: str = Field(default="", max_length=120)
    macro_cycle: Text = ""
    industry_cycle: Text = ""
    valuation_cycle: Text = ""
    business: Text = ""
    moat: Text = ""
    quality: Text = ""
    valuation: Text = ""
    evidence_notes: Text = ""
    entry_price: Price | None = None
    fair_value: Price | None = None
    estimated_cost_pct: Percent | None = None
    stop_loss_price: Price | None = None
    scenarios: InvestmentScenarios = Field(default_factory=InvestmentScenarios)
    risk_inputs: InvestmentRiskInputs = Field(default_factory=InvestmentRiskInputs)
    action_rules: InvestmentActionRules = Field(default_factory=InvestmentActionRules)
    invalidation: Text = ""
    review_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    review_notes: Text = ""
    status: Literal["draft", "watching", "holding", "reviewed", "archived"] = "draft"

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9^][A-Z0-9._^=\-]*", value):
            raise ValueError("请输入有效证券代码")
        return value

    @field_validator("analysis_date", "review_on")
    @classmethod
    def valid_date(cls, value):
        if value is not None and (date.fromisoformat(value).isoformat() != value or value < "1970-01-01"):
            raise ValueError("请输入1970年后的有效日期")
        return value

    @model_validator(mode="after")
    def coherent_dates_and_stop(self):
        if self.review_on and self.review_on < self.analysis_date:
            raise ValueError("复核日期不能早于计划分析日期")
        if self.stop_loss_price is not None and self.entry_price is not None and self.stop_loss_price >= self.entry_price:
            raise ValueError("多头保护参考价必须低于计划入场价")
        return self


class InvestmentPlanUpdate(InvestmentPlanWrite):
    revision: int = Field(ge=1, strict=True)
    rebase_policy: bool = False
    mark_reviewed: bool = False


class InvestmentRuleCheck(ContractModel):
    key: str
    label: str
    status: Literal["within_limit", "breach", "unknown", "not_applicable"]
    actual: float | None = None
    limit: float | None = None
    message: str


class InvestmentQuestion(ContractModel):
    key: str
    question: str
    status: Literal["recorded", "missing"]
    answer: str


class InvestmentEvaluation(ContractModel):
    status: Literal["incomplete", "breach", "within_configured_limits"]
    margin_of_safety_pct: float | None = None
    gross_expected_return_pct: float | None = None
    net_expected_return_pct: float | None = None
    reward_risk_ratio: float | None = None
    bear_return_pct: float | None = None
    bull_return_pct: float | None = None
    scenario_portfolio_loss_pct: float | None = None
    zero_value_portfolio_loss_pct: float | None = None
    stop_reference_loss_pct: float | None = None
    planned_cash_usd: float | None = None
    planned_cash_pct: float | None = None
    checks: list[InvestmentRuleCheck]
    questions: list[InvestmentQuestion]
    notices: list[str]
    eligible_for_rating: Literal[False] = False
    scope_notice: str = "仅按用户自填假设计算纪律与情景；不验证公允价值、概率或盈利能力，不构成买卖判断或执行交易。"


class InvestmentPlanView(InvestmentPlanWrite):
    id: str
    revision: int
    policy_snapshot: InvestmentPolicyView
    evaluation: InvestmentEvaluation
    review_due: bool
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    scope_notice: str = "用户手动维护的研究与决策记录；holding为本人标签，不代表已连接券商或核实真实持仓。"


class InvestmentPlanList(ContractModel):
    items: list[InvestmentPlanView]
