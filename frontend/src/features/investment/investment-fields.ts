import type {
  InvestmentActionRules,
  InvestmentPlanUpdate,
  InvestmentPlanView,
  InvestmentPlanWrite,
  InvestmentPolicyUpdate,
  InvestmentPolicyView,
  InvestmentRiskInputs,
  InvestmentScenario,
} from "../../shared/api/generated/models";

export type PlanFields = InvestmentPlanWrite & {
  rebase_policy?: boolean;
  mark_reviewed?: boolean;
};

const text = (value: string | undefined | null) => value?.trim() ?? "";
const optionalNumber = (value: number | undefined | null) => {
  if (value === undefined || value === null) return null;
  if (!Number.isFinite(value)) throw new Error("金额、概率和比例须为有效数字；未知时请留空。");
  return value;
};

export function actionRules(fields: InvestmentActionRules | undefined): InvestmentActionRules {
  return {
    buy: text(fields?.buy),
    add: text(fields?.add),
    reduce: text(fields?.reduce),
    exit: text(fields?.exit),
    review: text(fields?.review),
  };
}

export function investmentPolicyUpdate(
  fields: InvestmentPolicyView,
  revision: number,
): InvestmentPolicyUpdate {
  return {
    revision,
    name: text(fields.name),
    enabled: fields.enabled ?? true,
    objective: text(fields.objective),
    style: fields.style ?? "long_term",
    review_interval_days: optionalNumber(fields.review_interval_days),
    principles: {
      goal: text(fields.principles?.goal),
      cycles: text(fields.principles?.cycles),
      quality: text(fields.principles?.quality),
      valuation: text(fields.principles?.valuation),
      sizing: text(fields.principles?.sizing),
      probability: text(fields.principles?.probability),
      discipline: text(fields.principles?.discipline),
      life: text(fields.principles?.life),
    },
    action_rules: actionRules(fields.action_rules),
    risk_limits: {
      max_single_position_pct: optionalNumber(fields.risk_limits?.max_single_position_pct),
      max_theme_exposure_pct: optionalNumber(fields.risk_limits?.max_theme_exposure_pct),
      min_cash_pct: optionalNumber(fields.risk_limits?.min_cash_pct),
      max_drawdown_pct: optionalNumber(fields.risk_limits?.max_drawdown_pct),
      max_loss_per_idea_pct: optionalNumber(fields.risk_limits?.max_loss_per_idea_pct),
      minimum_margin_of_safety_pct: optionalNumber(
        fields.risk_limits?.minimum_margin_of_safety_pct,
      ),
      min_reward_risk: optionalNumber(fields.risk_limits?.min_reward_risk),
    },
    life_notes: text(fields.life_notes),
    tax_notes: text(fields.tax_notes),
  };
}

function scenario(fields: InvestmentScenario | undefined): InvestmentScenario {
  return {
    target_price: optionalNumber(fields?.target_price),
    probability_pct: optionalNumber(fields?.probability_pct),
    thesis: text(fields?.thesis),
  };
}

function riskInputs(fields: InvestmentRiskInputs | undefined): InvestmentRiskInputs {
  return {
    capital_total_usd: optionalNumber(fields?.capital_total_usd),
    available_cash_usd: optionalNumber(fields?.available_cash_usd),
    essential_cash_usd: optionalNumber(fields?.essential_cash_usd),
    current_position_pct: optionalNumber(fields?.current_position_pct),
    target_position_pct: optionalNumber(fields?.target_position_pct),
    current_theme_exposure_pct: optionalNumber(fields?.current_theme_exposure_pct),
    target_theme_exposure_pct: optionalNumber(fields?.target_theme_exposure_pct),
    current_drawdown_pct: optionalNumber(fields?.current_drawdown_pct),
    unlevered_long_only: fields?.unlevered_long_only ?? true,
  };
}

export function investmentPlanWrite(fields: PlanFields): InvestmentPlanWrite {
  const scenarios = {
    bear: scenario(fields.scenarios?.bear),
    base: scenario(fields.scenarios?.base),
    bull: scenario(fields.scenarios?.bull),
  };
  const probabilities = Object.values(scenarios).map((item) => item.probability_pct);
  if (
    probabilities.every((value): value is number => typeof value === "number") &&
    Math.abs(probabilities.reduce((total, value) => total + value, 0) - 100) > 0.000001
  )
    throw new Error("完整三情景概率必须合计 100%；未估计的概率请留空。");
  return {
    ticker: text(fields.ticker).toUpperCase(),
    analysis_date: fields.analysis_date,
    run_id: text(fields.run_id) || null,
    title: text(fields.title),
    theme: text(fields.theme),
    macro_cycle: text(fields.macro_cycle),
    industry_cycle: text(fields.industry_cycle),
    valuation_cycle: text(fields.valuation_cycle),
    business: text(fields.business),
    moat: text(fields.moat),
    quality: text(fields.quality),
    valuation: text(fields.valuation),
    evidence_notes: text(fields.evidence_notes),
    entry_price: optionalNumber(fields.entry_price),
    fair_value: optionalNumber(fields.fair_value),
    estimated_cost_pct: optionalNumber(fields.estimated_cost_pct),
    stop_loss_price: optionalNumber(fields.stop_loss_price),
    scenarios,
    risk_inputs: riskInputs(fields.risk_inputs),
    action_rules: actionRules(fields.action_rules),
    invalidation: text(fields.invalidation),
    review_on: text(fields.review_on) || null,
    review_notes: text(fields.review_notes),
    status: fields.status ?? "draft",
  };
}

export function investmentPlanUpdate(fields: PlanFields, revision: number): InvestmentPlanUpdate {
  return {
    ...investmentPlanWrite(fields),
    revision,
    rebase_policy: fields.rebase_policy ?? false,
    mark_reviewed: fields.mark_reviewed ?? false,
  };
}

export function newInvestmentPlan(
  policy: InvestmentPolicyView,
  today: string,
  ticker = "",
  runId: string | null = null,
): PlanFields {
  let reviewOn: string | null = null;
  if (policy.review_interval_days) {
    const date = new Date(`${today}T00:00:00Z`);
    if (Number.isFinite(date.getTime())) {
      date.setUTCDate(date.getUTCDate() + policy.review_interval_days);
      reviewOn = date.toISOString().slice(0, 10);
    }
  }
  return {
    ticker,
    analysis_date: today,
    run_id: runId,
    status: "draft",
    review_on: reviewOn,
    action_rules: actionRules(policy.action_rules),
    scenarios: { bear: {}, base: {}, bull: {} },
    risk_inputs: { unlevered_long_only: true },
    rebase_policy: false,
    mark_reviewed: false,
  };
}

export function filterInvestmentPlans(
  items: readonly InvestmentPlanView[],
  options: { search: string; status: string; dueOnly: boolean; includeArchived: boolean },
): InvestmentPlanView[] {
  const query = options.search.trim().toLocaleLowerCase();
  return items.filter(
    (item) =>
      (options.includeArchived || item.status !== "archived") &&
      (options.status === "all" || item.status === options.status) &&
      (!options.dueOnly || (item.review_due && item.status !== "archived")) &&
      (!query ||
        [item.ticker, item.theme, item.title].some((value) =>
          value?.toLocaleLowerCase().includes(query),
        )),
  );
}

export const policyPrincipleLabels = [
  ["goal", "长期目标"],
  ["cycles", "周期与趋势"],
  ["quality", "赛道与公司质量"],
  ["valuation", "估值与安全边际"],
  ["sizing", "资金与仓位"],
  ["probability", "概率与情景"],
  ["discipline", "操作纪律"],
  ["life", "生活与流动性边界"],
] as const;

export const actionRuleLabels = [
  ["buy", "买入规则"],
  ["add", "加仓规则"],
  ["reduce", "减仓规则"],
  ["exit", "退出规则"],
  ["review", "复盘规则"],
] as const;

export const riskLimitLabels = [
  ["max_single_position_pct", "单股仓位上限", "%"],
  ["max_theme_exposure_pct", "主题集中度上限", "%"],
  ["min_cash_pct", "最低现金比例", "%"],
  ["max_drawdown_pct", "组合回撤边界", "%"],
  ["max_loss_per_idea_pct", "单笔情景亏损边界", "%"],
  ["minimum_margin_of_safety_pct", "最低安全边际", "%"],
  ["min_reward_risk", "最低盈亏比", "倍"],
] as const;

export const investmentStatusLabels = {
  draft: "草稿",
  watching: "观察中",
  holding: "本人标记持有",
  reviewed: "已复盘",
  archived: "已归档",
};

export const investmentEvaluationLabels = {
  incomplete: "待补齐判断与边界",
  breach: "存在超限事项",
  within_configured_limits: "已设规则内 · 仍需本人判断",
};

export function formatInvestmentNumber(value: number | undefined | null, suffix = "") {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "待补充"
    : `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}${suffix}`;
}
