import { describe, expect, it } from "vitest";
import type { ManifestResponse, TemplateView } from "../../shared/api/generated/models";
import {
  CURRENT_MODEL_CONFIGURATION,
  modelBindingSummary,
  qualifiedResearchRating,
  researchContextIds,
  researchDefaultTab,
  researchPrefill,
  researchPrefillUrl,
  researchRequest,
  sourceTemplateId,
} from "./research-setup";

const template: TemplateView = {
  id: "template-v2",
  template_id: "family",
  version: 2,
  name: "标准",
  default_profile_id: "model",
  analysts: ["market", "news"],
  depth: 2,
  language: "Chinese",
  role_bindings: {},
  created_at: "2026-10-25T01:00:00Z",
};
const manifest = (patch: Partial<ManifestResponse> = {}): ManifestResponse => ({
  schema_version: "1",
  graph_contract_version: "1",
  workspace_id: "w",
  owner_id: "o",
  asset_type: "stock",
  memory_scope: "run",
  runtime: {},
  selection: {},
  request: {
    ticker: "NVDA",
    date: "2026-10-01",
    analysts: ["fundamentals"],
    depth: 1,
    language: "English",
    checkpoint: true,
    template_version_id: "template-v1",
  },
  ...patch,
});

describe("reviewable research setup", () => {
  it("rerun links only navigate to an explicit setup mode", () => {
    expect(researchPrefillUrl("run /1", "current")).toBe(
      "/research/new?from_run=run+%2F1&prefill=current",
    );
    expect(researchPrefillUrl("run", "original")).toContain("prefill=original");
  });
  it("updates date and current template settings without mutating the old manifest", () => {
    const source = manifest();
    const result = researchPrefill(source, "current", "2026-10-25", [template], template);
    expect(result).toMatchObject({
      date: "2026-10-25",
      ticker: "NVDA",
      template_version_id: template.id,
      analysts: ["market", "news"],
      depth: 2,
      language: "Chinese",
    });
    expect(source.request.date).toBe("2026-10-01");
  });
  it("keeps original parameters but requires a choice when the old template is unavailable", () => {
    const result = researchPrefill(manifest(), "original", "2026-10-25", [template], template);
    expect(result).toMatchObject({ date: "2026-10-01", analysts: ["fundamentals"], depth: 1 });
    expect(result.template_version_id).toBeUndefined();
  });
  it("finds the frozen template when the original request used the then-default", () => {
    const source = manifest({
      request: { ticker: "NVDA", date: "2026-10-01", template_version_id: null },
      selection: { model_snapshot: { template_version_id: template.id } },
    });
    expect(sourceTemplateId(source)).toBe(template.id);
    expect(researchPrefill(source, "original", "2026-10-25", [template]).template_version_id).toBe(
      template.id,
    );
  });
  it("does not silently replace unknown local history with current model configuration", () => {
    const source = manifest({
      request: { ticker: "NVDA", date: "2026-10-01", template_version_id: null },
    });
    expect(
      researchPrefill(source, "original", "2026-10-25", [], template).template_version_id,
    ).toBeUndefined();
    expect(researchPrefill(source, "current", "2026-10-25", []).template_version_id).toBe(
      CURRENT_MODEL_CONFIGURATION,
    );
    expect(
      researchRequest({
        ticker: " nvda ",
        date: "2026-10-25",
        template_version_id: CURRENT_MODEL_CONFIGURATION,
      }),
    ).toMatchObject({ ticker: "NVDA", template_version_id: null });
  });
  it("defaults terminal views to conclusions while preserving a user-selected tab", () => {
    expect(researchDefaultTab("running")).toBe("workflow");
    for (const status of ["completed", "failed", "cancelled", "interrupted"])
      expect(researchDefaultTab(status)).toBe("conclusion");
    expect(researchDefaultTab("completed", "events")).toBe("events");
  });
  it("summarizes only supplied model assignments without inventing a current fallback", () => {
    expect(modelBindingSummary({ label: "历史", fallbackByTier: false, roles: {} })).toBe(
      "模型信息未确认",
    );
    expect(
      modelBindingSummary({
        label: "历史",
        fallbackByTier: false,
        roles: {
          market: { source: "manifest", model: "m", provider: "v" },
          news: { source: "manifest", model: "m", provider: "v" },
        },
      }),
    ).toBe("v / m");
  });
  it("does not publish historical or insufficiently qualified raw ratings as effective judgments", () => {
    expect(qualifiedResearchRating({ rating: "BUY" })).toBeUndefined();
    const quality = {
      status: "degraded",
      evidence_count: 1,
      validated_price: true,
      eligible_for_rating: false,
      eligible_for_levels: false,
      eligible_for_sizing: false,
      required_categories: [],
      available_categories: [],
      missing_categories: [],
      issues: [],
    } as const;
    expect(
      qualifiedResearchRating({
        rating: "BUY",
        quality: {
          ...quality,
          required_categories: [],
          available_categories: [],
          missing_categories: [],
          issues: [],
        },
      }),
    ).toBeUndefined();
    expect(
      qualifiedResearchRating({
        rating: "HOLD",
        quality: {
          ...quality,
          eligible_for_rating: true,
          required_categories: [],
          available_categories: [],
          missing_categories: [],
          issues: [],
        },
      }),
    ).toBe("HOLD");
  });
  it("only reuses old context snapshots in original-date setup; current setup starts without stale attachments", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    const source = manifest({ request: { ticker: "NVDA", date: "2026-10-01", context_ids: [id] } });
    expect(researchPrefill(source, "original", "2026-10-25", []).context_ids).toEqual([id]);
    expect(researchPrefill(source, "current", "2026-10-25", []).context_ids).toEqual([]);
  });
  it("deduplicates, bounds and validates attachment IDs while explaining dropped inputs", () => {
    const ids = Array.from(
      { length: 6 },
      (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
    );
    expect(researchContextIds([ids[0]!, ids[0]!]).ids).toEqual([ids[0]]);
    expect(researchContextIds(ids).ids).toHaveLength(5);
    expect(researchContextIds(ids).warning).toContain("最多附加5");
    expect(researchContextIds(["bad"]).ids).toEqual([]);
    expect(researchContextIds(["bad"]).warning).toContain("无效资料ID");
  });
});
