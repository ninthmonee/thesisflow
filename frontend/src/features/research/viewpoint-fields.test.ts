import { describe, expect, it } from "vitest";
import type { ViewpointView } from "../../shared/api/generated/models";
import { confirmedViewpoint, viewpointFields, viewpointWrite } from "./viewpoint-fields";

describe("explicitly human-confirmed research viewpoints", () => {
  it("starts blank and unconfirmed instead of adopting an agent report", () => {
    expect(viewpointFields()).toMatchObject({
      thesis: "",
      invalidation: "",
      stance: "watch",
      confirmed: false,
      mark_reviewed: false,
    });
    expect(() =>
      confirmedViewpoint("run", { ...viewpointFields(), thesis: "论据", invalidation: "条件" }),
    ).toThrow("本人");
  });
  it("requires nonempty rationale and invalidation even when confirmed", () => {
    expect(() =>
      confirmedViewpoint("run", {
        ...viewpointFields(),
        confirmed: true,
        thesis: "  ",
        invalidation: "条件",
      }),
    ).toThrow("非空");
  });
  it("does not send internal status or automatic review marks in a new confirmation", () => {
    const payload = confirmedViewpoint("run", {
      ...viewpointFields(),
      confirmed: true,
      thesis: "  事实依据  ",
      invalidation: "  关键条件  ",
      review_notes: "  复核说明  ",
      mark_reviewed: true,
      status: "archived",
    });
    expect(payload).toEqual({
      run_id: "run",
      confirmed: true,
      stance: "watch",
      thesis: "事实依据",
      invalidation: "关键条件",
      review_on: null,
      review_notes: "复核说明",
    });
    expect(payload).not.toHaveProperty("mark_reviewed");
    expect(payload).not.toHaveProperty("status");
  });
  it("preserves editable saved content while requiring a fresh explicit reviewed action", () => {
    const source: ViewpointView = {
      id: "view",
      run_id: "run",
      ticker: "NVDA",
      thesis: "我的论据",
      invalidation: "我的条件",
      status: "active",
      stance: "positive",
      review_on: "2026-11-01",
      review_due: false,
      review_notes: "旧记录",
      source_quality: "degraded",
      revision: 2,
      reviewed_at: "2026-10-25T00:00:00Z",
      confirmed_at: "2026-10-25T00:00:00Z",
      created_at: "2026-10-25T00:00:00Z",
      updated_at: "2026-10-25T00:00:00Z",
    };
    const values = viewpointFields(source);
    expect(values).toMatchObject({
      thesis: source.thesis,
      invalidation: source.invalidation,
      confirmed: false,
      mark_reviewed: false,
    });
    expect(viewpointWrite({ ...values, status: "invalidated", mark_reviewed: true })).toMatchObject(
      { status: "invalidated", mark_reviewed: true },
    );
    expect(source.revision).toBe(2);
  });
});
