import {
  createInvestmentPlan,
  createViewpoint,
  getInvestmentPlan,
  getInvestmentPolicy,
  listInvestmentPlans,
  listViewpoints,
  updateInvestmentPlan,
  updateInvestmentPolicy,
  updateViewpoint,
} from "../../shared/api/generated/client";
import type {
  InvestmentPlanView,
  InvestmentPolicyView,
  InvestmentPlanWriteStatus,
  ViewpointView,
  ViewpointCreateStance,
} from "../../shared/api/generated/models";
import {
  investmentPlanUpdate,
  investmentPlanWrite,
  investmentPolicyUpdate,
} from "../investment/investment-fields";

type Input = Record<string, unknown>;
const record = (value: unknown): Input => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("请求内容不完整，请重新打开编辑器。");
  return value as Input;
};
const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
const optional = (value: unknown) => {
  if (value === "" || value === null || value === undefined) return null;
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error("请输入有效数字，未知时请留空。");
  return result;
};
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
const planStatus = (value: unknown): InvestmentPlanWriteStatus => {
  if (
    value === "watching" ||
    value === "holding" ||
    value === "reviewed" ||
    value === "archived" ||
    value === "draft"
  )
    return value;
  throw new Error("计划状态不正确，请重新选择。");
};
const stance = (value: unknown): ViewpointCreateStance => {
  if (value === "watch" || value === "positive" || value === "neutral" || value === "negative")
    return value;
  throw new Error("请选择有效的研究立场。");
};

function mappedPlan(plan: InvestmentPlanView) {
  return {
    id: plan.id,
    symbol: plan.ticker,
    title: plan.title ?? "",
    status: plan.status ?? "draft",
    business: plan.business ?? "",
    cycle: plan.industry_cycle ?? "",
    moat: plan.moat ?? "",
    valuation: plan.valuation ?? "",
    invalidation: plan.invalidation ?? "",
    review: plan.review_on ?? "",
    reviewed: plan.reviewed_at?.slice(0, 10) ?? "",
    reviewNotes: plan.review_notes ?? "",
    position: plan.risk_inputs?.target_position_pct ?? "",
    referencePrice: plan.entry_price ?? "",
    fairValue: plan.fair_value ?? "",
    scenarios: ["bear", "base", "bull"].map((key) => {
      const value = plan.scenarios?.[key as "bear" | "base" | "bull"];
      return { price: value?.target_price ?? "", probability: value?.probability_pct ?? "" };
    }),
    revision: plan.revision,
    runId: plan.run_id ?? "",
    analysisDate: plan.analysis_date,
    reviewDue: plan.review_due,
    evaluation: plan.evaluation,
    policySnapshot: plan.policy_snapshot,
  };
}

function mappedPolicy(policy: InvestmentPolicyView) {
  return {
    objective: policy.objective ?? "",
    maxPosition: policy.risk_limits?.max_single_position_pct ?? "",
    minCash: policy.risk_limits?.min_cash_pct ?? "",
    interval: policy.review_interval_days ?? "",
    revision: policy.revision,
    saved: policy.saved,
    name: policy.name ?? "个人投资体系",
    principles: policy.principles ?? {},
  };
}

function mappedViewpoint(view: ViewpointView) {
  return {
    id: view.id,
    revision: view.revision,
    thesis: view.thesis,
    invalidation: view.invalidation,
    stance: view.stance ?? "watch",
    review: view.review_on ?? "",
    confirmed: true,
    reviewDue: view.review_due,
    sourceQuality: view.source_quality,
  };
}

/** Each authenticated iframe owns a new service, so cached revisions never cross identities. */
export function createInvestmentService() {
  const plans = new Map<string, InvestmentPlanView>();
  const viewpoints = new Map<string, ViewpointView>();
  let policy: InvestmentPolicyView | undefined;

  async function planById(id: string) {
    const value = plans.get(id) ?? (await getInvestmentPlan(id));
    plans.set(id, value);
    return value;
  }

  async function load() {
    const [planResult, policyResult, viewResult] = await Promise.all([
      listInvestmentPlans({ include_archived: true }),
      getInvestmentPolicy(),
      listViewpoints({ include_archived: false }),
    ]);
    plans.clear();
    viewpoints.clear();
    planResult.items.forEach((plan) => plans.set(plan.id, plan));
    viewResult.items.forEach((view) => {
      if (!viewpoints.has(view.run_id)) viewpoints.set(view.run_id, view);
    });
    policy = policyResult;
    return {
      plans: planResult.items.map(mappedPlan),
      policy: mappedPolicy(policyResult),
      viewpoints: Object.fromEntries(
        [...viewpoints].map(([id, view]) => [id, mappedViewpoint(view)]),
      ),
    };
  }

  return async function investmentRequest(method: string, payload: unknown): Promise<unknown> {
    if (
      ![
        "investment.load",
        "investment.savePlan",
        "investment.archivePlan",
        "investment.restorePlan",
        "investment.savePolicy",
        "investment.loadViewpoints",
        "investment.saveViewpoint",
      ].includes(method)
    )
      return undefined;
    if (method === "investment.load") return load();
    const data = record(payload);
    if (method === "investment.savePlan") {
      const id = text(data.id);
      const original = id ? await planById(id) : undefined;
      const title = text(data.title);
      if (!title) throw new Error("请先为计划填写名称。");
      const symbol = text(data.symbol).toUpperCase();
      if (!symbol) throw new Error("请填写研究证券代码。");
      const scenarioRows = Array.isArray(data.scenarios) ? data.scenarios.map(record) : [];
      if (scenarioRows.length !== 3) throw new Error("请保留悲观、基准和乐观三个情景。");
      const scenarios = { ...original?.scenarios };
      (["bear", "base", "bull"] as const).forEach((key, index) => {
        scenarios[key] = {
          ...original?.scenarios?.[key],
          target_price: optional(scenarioRows[index]?.price),
          probability_pct: optional(scenarioRows[index]?.probability),
        };
      });
      const fields = {
        ...original,
        ticker: symbol,
        analysis_date: original?.analysis_date ?? (text(data.analysisDate) || today()),
        title,
        status: planStatus(data.status),
        business: text(data.business),
        industry_cycle: text(data.cycle),
        moat: text(data.moat),
        valuation: text(data.valuation),
        invalidation: text(data.invalidation),
        entry_price: optional(data.referencePrice),
        fair_value: optional(data.fairValue),
        review_on: text(data.review) || null,
        review_notes: text(data.reviewNotes),
        run_id: text(data.runId) || null,
        mark_reviewed: data.markReviewed === true,
        risk_inputs: {
          ...original?.risk_inputs,
          target_position_pct: optional(data.position),
        },
        scenarios,
      };
      if (fields.mark_reviewed && !fields.review_notes) throw new Error("请先填写本次复盘记录。");
      if (original && data.revision !== original.revision)
        throw new Error("计划已被更新，请刷新后重新编辑。");
      let saved = original
        ? await updateInvestmentPlan(original.id, investmentPlanUpdate(fields, original.revision))
        : await createInvestmentPlan(investmentPlanWrite(fields));
      if (!original && fields.mark_reviewed)
        saved = await updateInvestmentPlan(
          saved.id,
          investmentPlanUpdate({ ...saved, mark_reviewed: true }, saved.revision),
        );
      plans.set(saved.id, saved);
      return mappedPlan(saved);
    }
    if (method === "investment.archivePlan" || method === "investment.restorePlan") {
      const original = await planById(text(data.id));
      const saved = await updateInvestmentPlan(
        original.id,
        investmentPlanUpdate(
          { ...original, status: method === "investment.archivePlan" ? "archived" : "watching" },
          original.revision,
        ),
      );
      plans.set(saved.id, saved);
      return mappedPlan(saved);
    }
    if (method === "investment.savePolicy") {
      const original = policy ?? (await getInvestmentPolicy());
      if (data.revision !== undefined && data.revision !== original.revision)
        throw new Error("投资体系已更新，请刷新后重新编辑。");
      const fields = {
        ...original,
        objective: text(data.objective),
        review_interval_days: optional(data.interval),
        risk_limits: {
          ...original.risk_limits,
          max_single_position_pct: optional(data.maxPosition),
          min_cash_pct: optional(data.minCash),
        },
      };
      policy = await updateInvestmentPolicy(investmentPolicyUpdate(fields, original.revision));
      return mappedPolicy(policy);
    }
    if (method === "investment.loadViewpoints") {
      const runId = text(data.runId);
      const result = await listViewpoints({ run_id: runId, include_archived: false });
      const view = result.items[0];
      if (view) viewpoints.set(runId, view);
      else viewpoints.delete(runId);
      return view ? mappedViewpoint(view) : null;
    }
    if (method === "investment.saveViewpoint") {
      const runId = text(data.runId);
      const original = viewpoints.get(runId);
      const body = {
        thesis: text(data.thesis),
        invalidation: text(data.invalidation),
        stance: stance(data.stance),
        review_on: text(data.review) || null,
      };
      if (!runId || !body.thesis || !body.invalidation || data.confirmed !== true)
        throw new Error("请完整填写本人观点与失效条件，并确认这是你本人整理的观点。");
      const saved = original
        ? await updateViewpoint(original.id, {
            ...body,
            revision: original.revision,
            review_notes: original.review_notes,
            status: original.status,
          })
        : await createViewpoint({ ...body, run_id: runId, confirmed: true });
      viewpoints.set(runId, saved);
      return mappedViewpoint(saved);
    }
    return undefined;
  };
}
