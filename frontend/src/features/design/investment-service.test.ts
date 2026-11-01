import { beforeEach, describe, expect, it, vi } from "vitest";
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
import type { InvestmentPlanView, InvestmentPolicyView } from "../../shared/api/generated/models";
import { createInvestmentService } from "./investment-service";

vi.mock("../../shared/api/generated/client", () => ({
  createInvestmentPlan: vi.fn<typeof createInvestmentPlan>(),
  createViewpoint: vi.fn<typeof createViewpoint>(),
  getInvestmentPlan: vi.fn<typeof getInvestmentPlan>(),
  getInvestmentPolicy: vi.fn<typeof getInvestmentPolicy>(),
  listInvestmentPlans: vi.fn<typeof listInvestmentPlans>(),
  listViewpoints: vi.fn<typeof listViewpoints>(),
  updateInvestmentPlan: vi.fn<typeof updateInvestmentPlan>(),
  updateInvestmentPolicy: vi.fn<typeof updateInvestmentPolicy>(),
  updateViewpoint: vi.fn<typeof updateViewpoint>(),
}));

function policy(): InvestmentPolicyView {
  return {
    revision: 12,
    saved: true,
    name: "现金流与周期",
    enabled: false,
    style: "left_side",
    objective: "保持流动性",
    review_interval_days: 30,
    life_notes: "未来两年的生活费",
    tax_notes: "复核税务口径",
    principles: {
      goal: "个人长期目标",
      cycles: "核实行业周期",
      quality: "现金流质量",
      valuation: "估值边界",
      sizing: "分批观察",
      probability: "记录相反情景",
      discipline: "坚持复核",
      life: "优先生活现金",
    },
    action_rules: {
      buy: "买入条件",
      add: "加仓条件",
      reduce: "减仓条件",
      exit: "退出条件",
      review: "复核条件",
    },
    risk_limits: {
      max_single_position_pct: 15,
      min_cash_pct: 30,
      max_theme_exposure_pct: 25,
      max_drawdown_pct: 10,
      max_loss_per_idea_pct: 2,
      minimum_margin_of_safety_pct: 20,
      min_reward_risk: 3,
    },
  };
}

function plan(): InvestmentPlanView {
  return {
    id: "plan-one",
    ticker: "NVDA",
    title: "核实现金流",
    analysis_date: "2026-10-20",
    status: "watching",
    revision: 7,
    created_at: "2026-10-20T10:00:00Z",
    updated_at: "2026-10-22T12:00:00Z",
    run_id: "run-one",
    business: "业务记录",
    industry_cycle: "行业周期记录",
    moat: "护城河记录",
    valuation: "估值记录",
    invalidation: "失效条件",
    theme: "算力",
    macro_cycle: "宏观周期记录",
    valuation_cycle: "估值周期记录",
    quality: "盈利质量记录",
    evidence_notes: "原有证据备注",
    entry_price: 100,
    fair_value: 120,
    estimated_cost_pct: 0.4,
    stop_loss_price: 80,
    review_on: "2099-01-01",
    reviewed_at: null,
    review_due: true,
    review_notes: "原有复盘内容",
    scenarios: {
      bear: { target_price: 75, probability_pct: 20, thesis: "悲观条件" },
      base: { target_price: 110, probability_pct: 60, thesis: "基准条件" },
      bull: { target_price: 140, probability_pct: 20, thesis: "乐观条件" },
    },
    risk_inputs: {
      capital_total_usd: 100000,
      available_cash_usd: 50000,
      essential_cash_usd: 20000,
      current_position_pct: 5,
      target_position_pct: 10,
      current_theme_exposure_pct: 8,
      target_theme_exposure_pct: 20,
      current_drawdown_pct: 2,
      unlevered_long_only: false,
    },
    action_rules: {
      buy: "旧买入条件",
      add: "旧加仓条件",
      reduce: "旧减仓条件",
      exit: "旧退出条件",
      review: "旧复核条件",
    },
    policy_snapshot: policy(),
    evaluation: { status: "incomplete", checks: [], questions: [], notices: ["尚未核实"] },
  };
}

async function loadedService() {
  const service = createInvestmentService();
  const loaded = (await service("investment.load", undefined)) as {
    plans: Array<Record<string, unknown>>;
    policy: Record<string, unknown>;
  };
  return { service, loaded, draft: loaded.plans[0]! };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getInvestmentPolicy).mockResolvedValue(policy());
  vi.mocked(listInvestmentPlans).mockResolvedValue({ items: [plan()] });
  vi.mocked(listViewpoints).mockResolvedValue({ items: [] });
});

describe("authenticated design investment adapter", () => {
  it("preserves all policy fields outside the compact editor when saving a new version", async () => {
    const { service, loaded } = await loadedService();
    vi.mocked(updateInvestmentPolicy).mockResolvedValue({ ...policy(), revision: 13 });
    await service("investment.savePolicy", {
      ...loaded.policy,
      objective: "新的目标",
      maxPosition: "12",
      minCash: "35",
      interval: "21",
    });
    expect(updateInvestmentPolicy).toHaveBeenCalledWith({
      ...policy(),
      saved: undefined,
      revision: 12,
      objective: "新的目标",
      review_interval_days: 21,
      risk_limits: { ...policy().risk_limits, max_single_position_pct: 12, min_cash_pct: 35 },
    });
  });

  it("preserves unmapped plan fields, scenario rationales and risk inputs and uses the loaded revision", async () => {
    const { service, draft } = await loadedService();
    vi.mocked(updateInvestmentPlan).mockResolvedValue({
      ...plan(),
      revision: 8,
      title: "修改后的名称",
    });
    await service("investment.savePlan", { ...draft, title: "修改后的名称", position: "12" });
    expect(updateInvestmentPlan).toHaveBeenCalledWith(
      "plan-one",
      expect.objectContaining({
        revision: 7,
        title: "修改后的名称",
        analysis_date: "2026-10-20",
        macro_cycle: "宏观周期记录",
        valuation_cycle: "估值周期记录",
        quality: "盈利质量记录",
        evidence_notes: "原有证据备注",
        theme: "算力",
        estimated_cost_pct: 0.4,
        stop_loss_price: 80,
        scenarios: plan().scenarios,
        action_rules: plan().action_rules,
        risk_inputs: { ...plan().risk_inputs, target_position_pct: 12 },
        rebase_policy: false,
        mark_reviewed: false,
      }),
    );
    expect(vi.mocked(updateInvestmentPlan).mock.calls[0]?.[1]).not.toHaveProperty(
      "policy_snapshot",
    );
  });

  it("rejects a stale editor revision before sending a destructive full plan update", async () => {
    const { service, draft } = await loadedService();
    await expect(service("investment.savePlan", { ...draft, revision: 6 })).rejects.toThrow(
      "已被更新",
    );
    expect(updateInvestmentPlan).not.toHaveBeenCalled();
  });

  it.each(["holding", "reviewed"] as const)(
    "preserves the existing %s status when saving through the compact editor",
    async (status) => {
      vi.mocked(listInvestmentPlans).mockResolvedValue({ items: [{ ...plan(), status }] });
      const { service, draft } = await loadedService();
      vi.mocked(updateInvestmentPlan).mockResolvedValue({ ...plan(), status, revision: 8 });
      await service("investment.savePlan", draft);
      expect(updateInvestmentPlan).toHaveBeenCalledWith(
        "plan-one",
        expect.objectContaining({ status, revision: 7 }),
      );
    },
  );

  it("archives and restores saved plans with current server revisions and without clearing hidden fields", async () => {
    const { service } = await loadedService();
    vi.mocked(updateInvestmentPlan)
      .mockResolvedValueOnce({ ...plan(), status: "archived", revision: 8 })
      .mockResolvedValueOnce({ ...plan(), status: "watching", revision: 9 });
    await service("investment.archivePlan", { id: "plan-one" });
    await service("investment.restorePlan", { id: "plan-one" });
    expect(updateInvestmentPlan).toHaveBeenNthCalledWith(
      1,
      "plan-one",
      expect.objectContaining({
        revision: 7,
        status: "archived",
        evidence_notes: "原有证据备注",
        scenarios: plan().scenarios,
        risk_inputs: plan().risk_inputs,
      }),
    );
    expect(updateInvestmentPlan).toHaveBeenNthCalledWith(
      2,
      "plan-one",
      expect.objectContaining({
        revision: 8,
        status: "watching",
        evidence_notes: "原有证据备注",
        scenarios: plan().scenarios,
        risk_inputs: plan().risk_inputs,
      }),
    );
  });

  it("uses server due status and server review timestamp rather than a prototype observation date", async () => {
    const { service, draft } = await loadedService();
    expect(draft.reviewDue).toBe(true);
    vi.mocked(updateInvestmentPlan).mockResolvedValue({
      ...plan(),
      revision: 8,
      review_due: false,
      reviewed_at: "2030-04-05T12:13:14Z",
    });
    const saved = await service("investment.savePlan", {
      ...draft,
      markReviewed: true,
      reviewNotes: "重新核对了订单与现金流。",
    });
    expect(updateInvestmentPlan).toHaveBeenCalledWith(
      "plan-one",
      expect.objectContaining({ mark_reviewed: true, review_notes: "重新核对了订单与现金流。" }),
    );
    expect(vi.mocked(updateInvestmentPlan).mock.calls[0]?.[1]).not.toHaveProperty("reviewed_at");
    expect(saved).toMatchObject({ reviewDue: false, reviewed: "2030-04-05" });
  });

  it("requires review notes and persists a new plan before asking the server to mark it reviewed", async () => {
    const { service, draft } = await loadedService();
    await expect(
      service("investment.savePlan", { ...draft, markReviewed: true, reviewNotes: "" }),
    ).rejects.toThrow("复盘记录");
    expect(updateInvestmentPlan).not.toHaveBeenCalled();
    vi.mocked(createInvestmentPlan).mockResolvedValue({
      ...plan(),
      id: "new-plan",
      revision: 1,
      review_due: true,
    });
    vi.mocked(updateInvestmentPlan).mockResolvedValue({
      ...plan(),
      id: "new-plan",
      revision: 2,
      review_due: false,
      reviewed_at: "2026-11-01T10:11:12Z",
    });
    const saved = await service("investment.savePlan", {
      ...draft,
      id: null,
      revision: 0,
      markReviewed: true,
      reviewNotes: "首次整理并完成复核。",
    });
    expect(createInvestmentPlan).toHaveBeenCalledOnce();
    expect(updateInvestmentPlan).toHaveBeenCalledWith(
      "new-plan",
      expect.objectContaining({ revision: 1, mark_reviewed: true }),
    );
    expect(saved).toMatchObject({
      id: "new-plan",
      revision: 2,
      reviewDue: false,
      reviewed: "2026-11-01",
    });
  });

  it("does not share cached originals between authenticated service instances", async () => {
    await loadedService();
    vi.mocked(getInvestmentPlan).mockResolvedValue({
      ...plan(),
      title: "第二个工作区记录",
      revision: 99,
    });
    vi.mocked(updateInvestmentPlan).mockResolvedValue({
      ...plan(),
      status: "archived",
      revision: 100,
    });
    await createInvestmentService()("investment.archivePlan", { id: "plan-one" });
    expect(getInvestmentPlan).toHaveBeenCalledWith("plan-one");
    expect(updateInvestmentPlan).toHaveBeenCalledWith(
      "plan-one",
      expect.objectContaining({ revision: 99, title: "第二个工作区记录" }),
    );
  });

  it("leaves unsupported methods to the next domain handler", async () => {
    const request = createInvestmentService();
    expect(await request("investment.unknown", undefined)).toBeUndefined();
    expect(await request("research.load", undefined)).toBeUndefined();
  });
});
