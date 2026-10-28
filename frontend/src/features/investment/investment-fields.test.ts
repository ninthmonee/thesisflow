import { describe, expect, it } from "vitest";
import type { InvestmentPlanView, InvestmentPolicyView } from "../../shared/api/generated/models";
import {
  filterInvestmentPlans,
  formatInvestmentNumber,
  investmentPlanUpdate,
  investmentPlanWrite,
  investmentPolicyUpdate,
  newInvestmentPlan,
} from "./investment-fields";

const policy: InvestmentPolicyView = {
  revision: 3,
  saved: true,
  name: " 我的长期体系 ",
  enabled: true,
  action_rules: { buy: " 分批核实后买入 ", exit: " 基本假设失效时复核 " },
  risk_limits: { max_single_position_pct: 0 },
};

const plan = (id: string, values: Partial<InvestmentPlanView> = {}): InvestmentPlanView => ({
  ...investmentPlanWrite(newInvestmentPlan(policy, "2026-10-28", "NVDA")),
  id,
  revision: 4,
  policy_snapshot: policy,
  evaluation: { status: "incomplete", checks: [], questions: [], notices: [] },
  review_due: false,
  reviewed_at: null,
  created_at: "2026-10-28T00:00:00Z",
  updated_at: "2026-10-28T00:00:00Z",
  ...values,
});

describe("investment form boundaries", () => {
  it("preserves empty risk limits as unknown and keeps explicit zero", () => {
    const body = investmentPolicyUpdate(policy, 3);
    expect(body.name).toBe("我的长期体系");
    expect(body.risk_limits?.max_single_position_pct).toBe(0);
    expect(body.risk_limits?.min_cash_pct).toBeNull();
    expect(body.review_interval_days).toBeNull();
    expect(body).not.toHaveProperty("saved");
    expect(body).not.toHaveProperty("updated_at");
  });
  it("starts a draft with personal rules and never supplies prices or scenario probabilities", () => {
    const draft = newInvestmentPlan(policy, "2026-10-28", "NVDA", "own-research");
    const body = investmentPlanWrite(draft);
    expect(body.action_rules?.buy).toBe("分批核实后买入");
    expect(body.run_id).toBe("own-research");
    expect(body.entry_price).toBeNull();
    expect(body.risk_inputs?.target_position_pct).toBeNull();
    expect(body.scenarios?.bear?.probability_pct).toBeNull();
    expect(body.scenarios?.base?.probability_pct).toBeNull();
    expect(body.scenarios?.bull?.probability_pct).toBeNull();
    expect(body.status).toBe("draft");
  });
  it("prefills configured review intervals across years and allows an explicit clear", () => {
    const draft = newInvestmentPlan({ ...policy, review_interval_days: 2 }, "2026-12-31", "NVDA");
    expect(draft.review_on).toBe("2027-01-02");
    expect(investmentPlanWrite({ ...draft, review_on: "" }).review_on).toBeNull();
    expect(newInvestmentPlan(policy, "2026-12-31", "NVDA").review_on).toBeNull();
  });
  it("omits view metadata and preserves zero scenarios and explicit review intent", () => {
    const current = plan("p", {
      scenarios: { bear: { target_price: 0, probability_pct: 0 } },
      risk_inputs: { available_cash_usd: 0, unlevered_long_only: false },
    });
    const body = investmentPlanUpdate({ ...current, mark_reviewed: true }, 4);
    expect(body.scenarios?.bear?.target_price).toBe(0);
    expect(body.scenarios?.bear?.probability_pct).toBe(0);
    expect(body.risk_inputs?.available_cash_usd).toBe(0);
    expect(body.risk_inputs?.unlevered_long_only).toBe(false);
    expect(body.mark_reviewed).toBe(true);
    expect(body.rebase_policy).toBe(false);
    expect(body).not.toHaveProperty("evaluation");
    expect(body).not.toHaveProperty("policy_snapshot");
    expect(body).not.toHaveProperty("id");
  });
  it("requires complete probabilities to total 100 without normalizing the user's assumptions", () => {
    const draft = newInvestmentPlan(policy, "2026-10-28", "NVDA");
    expect(() =>
      investmentPlanWrite({
        ...draft,
        scenarios: {
          bear: { probability_pct: 10 },
          base: { probability_pct: 40 },
          bull: { probability_pct: 40 },
        },
      }),
    ).toThrow("合计 100%");
    const accepted = investmentPlanWrite({
      ...draft,
      scenarios: {
        bear: { probability_pct: 10 },
        base: { probability_pct: 40 },
        bull: { probability_pct: 50 },
      },
    });
    expect(accepted.scenarios?.bull?.probability_pct).toBe(50);
    expect(() =>
      investmentPlanWrite({ ...draft, scenarios: { bear: { probability_pct: 10 } } }),
    ).not.toThrow();
  });
  it("rejects non-finite budget input and does not render it as a valid result", () => {
    expect(() =>
      investmentPlanWrite({
        ticker: "NVDA",
        analysis_date: "2026-10-28",
        fair_value: Number.POSITIVE_INFINITY,
      }),
    ).toThrow("有效数字");
    expect(formatInvestmentNumber(Number.NaN, "%")).toBe("待补充");
    expect(formatInvestmentNumber(null)).toBe("待补充");
    expect(formatInvestmentNumber(0, "%")).toBe("0%");
  });
});

describe("decision and review filtering", () => {
  const items = [
    plan("due", { review_due: true, theme: "存储 / HBM", status: "watching" }),
    plan("reviewed", { ticker: "SOXX", status: "reviewed" }),
    plan("archive", { review_due: true, status: "archived", title: "旧计划" }),
  ];
  it("excludes archived plans from normal lists and due reviews", () => {
    expect(
      filterInvestmentPlans(items, {
        search: "",
        status: "all",
        dueOnly: false,
        includeArchived: false,
      }).map((item) => item.id),
    ).toEqual(["due", "reviewed"]);
    expect(
      filterInvestmentPlans(items, {
        search: "",
        status: "all",
        dueOnly: true,
        includeArchived: true,
      }).map((item) => item.id),
    ).toEqual(["due"]);
  });
  it("combines case-insensitive symbol and theme search with status while allowing archive recovery", () => {
    expect(
      filterInvestmentPlans(items, {
        search: " hbm ",
        status: "watching",
        dueOnly: false,
        includeArchived: false,
      }).map((item) => item.id),
    ).toEqual(["due"]);
    expect(
      filterInvestmentPlans(items, {
        search: "soxx",
        status: "reviewed",
        dueOnly: false,
        includeArchived: false,
      }).map((item) => item.id),
    ).toEqual(["reviewed"]);
    expect(
      filterInvestmentPlans(items, {
        search: "旧计划",
        status: "archived",
        dueOnly: false,
        includeArchived: true,
      }).map((item) => item.id),
    ).toEqual(["archive"]);
  });
});
