"""Deterministic checks of manual assumptions; no market or model inference."""

from tradingagents.web.api.investment_schemas import (
    InvestmentEvaluation,
    InvestmentPlanWrite,
    InvestmentPolicyWrite,
    InvestmentQuestion,
    InvestmentRuleCheck,
)


def _rounded(value):
    return round(value, 8) if value is not None else None


def evaluate_plan(plan: InvestmentPlanWrite, policy: InvestmentPolicyWrite) -> InvestmentEvaluation:
    price, fair, scenarios, inputs, limits = plan.entry_price, plan.fair_value, plan.scenarios, plan.risk_inputs, policy.risk_limits
    margin = (fair - price) / fair * 100 if price is not None and fair is not None else None
    bear = (scenarios.bear.target_price / price - 1) * 100 if price is not None and scenarios.bear.target_price is not None else None
    bull = (scenarios.bull.target_price / price - 1) * 100 if price is not None and scenarios.bull.target_price is not None else None
    ratio = bull / -bear if bear is not None and bull is not None and bear < 0 < bull else None
    parts = [scenarios.bear, scenarios.base, scenarios.bull]
    gross = None
    if price is not None and all(item.target_price is not None and item.probability_pct is not None for item in parts):
        gross = sum((item.target_price / price - 1) * item.probability_pct for item in parts)
    net = gross - plan.estimated_cost_pct if gross is not None and plan.estimated_cost_pct is not None else None
    target = inputs.target_position_pct
    scenario_loss = max(0, -bear) * target / 100 if bear is not None and target is not None and inputs.unlevered_long_only else None
    zero_loss = target if inputs.unlevered_long_only else None
    stop_loss = ((price - plan.stop_loss_price) / price * target
        if price is not None and plan.stop_loss_price is not None and target is not None and inputs.unlevered_long_only else None)
    cash = None
    if inputs.unlevered_long_only and all(value is not None for value in (inputs.capital_total_usd, inputs.available_cash_usd,
        inputs.current_position_pct, target)):
        cash = inputs.available_cash_usd - inputs.capital_total_usd * (target - inputs.current_position_pct) / 100
    cash_pct = cash / inputs.capital_total_usd * 100 if cash is not None else None
    checks = []

    def check(key, label, actual, limit, *, minimum=False, notice=""):
        unknown = actual is None or limit is None
        breach = not unknown and (actual < limit if minimum else actual > limit)
        status = "unknown" if unknown else "breach" if breach else "within_limit"
        reason = ("阈值尚未设置或输入不足，不能判定通过" if unknown
            else "超出本人配置边界" if breach else "仅在当前手填假设下符合已配置边界")
        checks.append(InvestmentRuleCheck(key=key, label=label, status=status,
            actual=_rounded(actual), limit=limit, message=reason + ("；" + notice if notice else "")))

    check("single_position", "单股仓位", target, limits.max_single_position_pct)
    check("theme_exposure", "主题集中度", inputs.target_theme_exposure_pct, limits.max_theme_exposure_pct)
    check("cash_reserve", "计划后现金比例", cash_pct, limits.min_cash_pct, minimum=True,
        notice="按当前与目标单股仓位差估算，不包含其他交易、费用或现金流")
    check("drawdown", "当前组合回撤", inputs.current_drawdown_pct, limits.max_drawdown_pct)
    check("idea_loss", "悲观情景的组合损失", scenario_loss, limits.max_loss_per_idea_pct,
        notice="悲观情景并非最大损失；缺少真实组合相关性与缺口风险")
    check("margin_of_safety", "安全边际", margin, limits.minimum_margin_of_safety_pct, minimum=True,
        notice="公允价值来自用户自填，未独立验证")
    check("reward_risk", "情景盈亏比", ratio, limits.min_reward_risk, minimum=True)
    check("essential_cash", "生活保留金", cash, inputs.essential_cash_usd, minimum=True)
    check("funding", "计划资金可行性", cash, 0, minimum=True,
        notice="负现金表示当前假设下资金不足，不取代本人更严格的现金储备边界")
    if not inputs.unlevered_long_only:
        checks.append(InvestmentRuleCheck(key="exposure_scope", label="杠杆与方向边界", status="unknown",
            message="此计算器只建模无杠杆多头；杠杆、空头与衍生品风险未计算"))
    questions = []
    entries = (
        ("industry", "行业趋势怎么样？", plan.industry_cycle),
        ("business", "公司靠什么赚钱？", plan.business),
        ("moat", "护城河是什么？", plan.moat),
        ("valuation", "当前估值贵不贵？", plan.valuation),
        ("downside", "判断错了会亏多少，失效条件是什么？", plan.invalidation),
        ("position", "计划仓位与资金约束是什么？", f"目标单股仓位 {target:.4g}%；仍需核对现金、集中度和情景" if target is not None else ""),
    )
    for key, question, answer in entries:
        questions.append(InvestmentQuestion(key=key, question=question,
            status="recorded" if answer.strip() else "missing", answer=answer))
    state = ("breach" if any(item.status == "breach" for item in checks)
        else "incomplete" if any(item.status == "unknown" for item in checks) or any(item.status == "missing" for item in questions)
        else "within_configured_limits")
    notices = [
        "所有估值、概率、周期及公司判断均为本人录入的假设；有填写不代表有证据或判断正确。",
        "概率不由模型自动生成或校准；完整三情景与概率后才计算毛期望收益，费用未知时净期望收益保持未知。",
        "零值损失仅为无杠杆多头单股归零对当前假设组合的影响，不是组合最大回撤或承诺损失上限。",
        "保护参考价无法保证成交，跳空、滑点、停牌可能使实际损失大于参考值；费用税务与汇率影响需另行核对。",
        "现金估算只反映该单股当前至目标仓位的变化，假定无其他资产及现金流变化；减仓回款未保证实现。",
        "本页复盘由本人手动维护；没有自动下单、后台预警或对历史收益的验证。",
    ]
    return InvestmentEvaluation(status=state, margin_of_safety_pct=_rounded(margin),
        gross_expected_return_pct=_rounded(gross), net_expected_return_pct=_rounded(net), reward_risk_ratio=_rounded(ratio),
        bear_return_pct=_rounded(bear), bull_return_pct=_rounded(bull), scenario_portfolio_loss_pct=_rounded(scenario_loss),
        zero_value_portfolio_loss_pct=_rounded(zero_loss), stop_reference_loss_pct=_rounded(stop_loss),
        planned_cash_usd=_rounded(cash), planned_cash_pct=_rounded(cash_pct), checks=checks, questions=questions, notices=notices)
