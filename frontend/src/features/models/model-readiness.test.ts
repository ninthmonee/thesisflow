import { describe, expect, it } from "vitest";
import type { ConnectionView, ProfileView, RoleView } from "../../shared/api/generated/models";
import { modelReadiness } from "./model-readiness";

const roles: RoleView[] = [
  { id: "market", label: "技术", tier: "quick" },
  { id: "portfolio_manager", label: "组合经理", tier: "deep" },
];
const profile = (patch: Partial<ProfileView> = {}): ProfileView => ({
  id: "p",
  connection_id: "c",
  connection_name: "自定义",
  provider: "custom",
  model_id: "m",
  parameters: {},
  capabilities: { tools: "supported" },
  revision: 1,
  test_status: "passed",
  tested_at: "2026-10-25T00:00:00Z",
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
  ...patch,
});
const connection = (patch: Partial<ConnectionView> = {}): ConnectionView => ({
  id: "c",
  api_protocol: "openai_chat_completions",
  auth_type: "none",
  credential_configured: false,
  credential_mask: null,
  enabled: true,
  endpoint: "http://localhost:11434/v1",
  header_names: [],
  headers_configured: false,
  name: "自定义",
  provider: "custom",
  resolved_protocol: "openai_chat_completions",
  revision: 1,
  scope: "private",
  vendor_name: "Local",
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
  ...patch,
});
const input = () => ({
  defaultId: "p",
  bindings: {},
  analysts: ["market"],
  roles,
  profiles: [profile()],
  connections: [connection()],
});

describe("read-only model readiness", () => {
  it("one tested model can be inherited by all roles without a key requirement for no-auth services", () => {
    const result = modelReadiness(input());
    expect(result.ready).toBe(true);
    expect(result.effective.every((item) => item.inherited && item.profile?.id === "p")).toBe(true);
  });
  it("an explicit missing override stays missing instead of inheriting the default", () => {
    const result = modelReadiness({ ...input(), bindings: { portfolio_manager: "deleted" } });
    expect(result.ready).toBe(false);
    expect(result.effective[1]?.profile).toBeUndefined();
    expect(result.issues).toContain("组合经理：模型配置不存在或未选择");
  });
  it("checks tools only for selected analysts, while stale chat tests and disabled connections remain blockers", () => {
    const noTools = { ...input(), profiles: [profile({ capabilities: { tools: false } })] };
    expect(modelReadiness(noTools).issues).toContain("技术：所选分析师需要工具检测通过");
    expect(
      modelReadiness({ ...noTools, analysts: ["news"] }).issues.some((issue) =>
        issue.includes("工具"),
      ),
    ).toBe(false);
    expect(
      modelReadiness({
        ...input(),
        profiles: [profile({ test_status: "stale" })],
        connections: [connection({ enabled: false })],
      }).ready,
    ).toBe(false);
  });
  it("stored subscription connection alone does not establish local authorization", () => {
    expect(
      modelReadiness({ ...input(), connections: [connection({ provider: "chatgpt" })] }).issues,
    ).toContain("技术：本机订阅授权未就绪");
  });
  it("does not call an unloaded role definition ready", () => {
    expect(modelReadiness({ ...input(), roles: [] }).ready).toBe(false);
  });
});
