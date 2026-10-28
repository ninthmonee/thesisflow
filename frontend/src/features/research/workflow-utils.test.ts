import { describe, expect, it } from "vitest";
import type { ProfileView, RunSnapshot } from "../../shared/api/generated/models";
import {
  modelsFromBootstrap,
  modelsFromManifest,
  modelsFromProfiles,
  workflowDepth,
  workflowNodeStatus,
} from "./workflow-utils";

function profile(id: string, model: string, provider = "openai"): ProfileView {
  return {
    id,
    model_id: model,
    provider,
    connection_id: `connection-${id}`,
    connection_name: `连接 ${id}`,
    capabilities: {},
    parameters: {},
    revision: 1,
    created_at: "2026-10-25T00:00:00Z",
    updated_at: "2026-10-25T00:00:00Z",
    test_status: "passed",
    tested_at: "2026-10-25T00:00:00Z",
  };
}

describe("workflow model provenance", () => {
  it("keeps a local Agent separate from host graph defaults and role bindings", () => {
    const result = modelsFromManifest({
      selection: {
        execution_mode: "local_agent",
        runtime_snapshot: { model_id: "requested-agent-model" },
      },
      runtime: { llm_provider: "host-provider", quick_think_llm: "host-model" },
    });
    expect(result.fallbackByTier).toBe(false);
    expect(result.roles).toEqual({});
    expect(result.label).toContain("requested-agent-model");
    expect(JSON.stringify(result)).not.toContain("host-model");
  });
  it("separates explicit draft role bindings from the inherited default", () => {
    const result = modelsFromProfiles(
      {
        default_profile_id: "main",
        role_bindings: { bull: "research", portfolio_manager: "gone" },
      },
      [profile("main", "gpt-main"), profile("research", "deepseek-research", "deepseek")],
      "draft",
    );
    expect(result.fallbackByTier).toBe(true);
    expect(result.roles.quick).toMatchObject({ model: "gpt-main", source: "draft" });
    expect(result.roles.deep).toEqual(result.roles.quick);
    expect(result.roles.bull).toEqual({
      provider: "deepseek",
      model: "deepseek-research",
      connectionName: "连接 research",
      source: "draft",
      testStatus: "passed",
    });
    expect(result.roles.portfolio_manager).toEqual({ source: "draft" });
    expect(result.roles.market).toBeUndefined();
  });

  it("keeps failed profile queries and deleted profiles unknown", () => {
    const result = modelsFromProfiles(
      { default_profile_id: "deleted", role_bindings: { bull: "deleted" } },
      [],
      "template",
    );
    expect(result.fallbackByTier).toBe(false);
    expect(result.roles.quick?.model).toBeUndefined();
    expect(result.roles.bull).toEqual({ source: "template" });
    expect(result.roles.market).toBeUndefined();
  });

  it("allows only known role keys from editable templates", () => {
    const result = modelsFromProfiles(
      { role_bindings: { __proto__: "main", credential_ref: "main", bull: "main" } },
      [profile("main", "gpt-main")],
      "template",
    );
    expect(Object.keys(result.roles)).toEqual(["bull"]);
  });

  it("reads historical frozen identities without consulting live profiles or tier fallback", () => {
    const result = modelsFromManifest({
      selection: {
        model_snapshot: {
          roles: {
            bull: { provider: "deepseek", model: "old-research" },
            quick: { provider: "openai", model: "old-quick" },
            deep: { provider: "anthropic", model: "old-deep" },
          },
        },
      },
      runtime: { quick_think_llm: "new-live-default" },
    });
    expect(result.roles.bull).toEqual({
      provider: "deepseek",
      model: "old-research",
      source: "manifest",
    });
    expect(result.roles.quick?.model).toBe("old-quick");
    expect(result.roles.portfolio_manager).toBeUndefined();
    expect(result.fallbackByTier).toBe(false);
    expect(JSON.stringify(result)).not.toContain("new-live-default");
  });

  it("copies only public model fields, dropping credential and endpoint metadata", () => {
    const result = modelsFromManifest({
      selection: {
        model_snapshot: {
          roles: {
            bull: {
              provider: "openai",
              model: "gpt-research",
              credential_ref: "private-keychain-reference",
              extra_header_ref: "private-header-reference",
              api_key: "private-secret-marker",
              base_url: "https://private-endpoint.invalid/?key=private-secret-marker",
              parameters: { headers: { Authorization: "private-secret-marker" } },
            },
            credential_ref: {
              provider: "private-keychain-reference",
              model: "private-secret-marker",
            },
          },
        },
      },
    });
    expect(result.roles.bull).toEqual({
      provider: "openai",
      model: "gpt-research",
      source: "manifest",
    });
    expect(JSON.stringify(result)).not.toContain("private-");
    expect(Object.keys(result.roles)).toEqual(["bull"]);
  });

  it.each([null, [], { roles: [] }, { roles: { bull: { model: ["wrong"] } } }])(
    "does not replace malformed frozen data with runtime defaults: %j",
    (snapshot) => {
      const result = modelsFromManifest({
        selection: { model_snapshot: snapshot },
        runtime: { quick_think_llm: "must-not-substitute" },
      });
      expect(result.fallbackByTier).toBe(false);
      expect(result.roles.bull?.model).toBeUndefined();
      expect(JSON.stringify(result)).not.toContain("must-not-substitute");
    },
  );

  it("rejects prototype inherited snapshot identities and invalid text", () => {
    const roles = Object.create({
      bull: { provider: "inherited", model: "inherited-model" },
    }) as Record<string, unknown>;
    roles.bear = { provider: "\u0000bad", model: " " };
    expect(modelsFromManifest({ selection: { model_snapshot: { roles } } }).roles).toEqual({
      bear: { source: "manifest" },
    });
  });

  it("preserves explicit legacy tier provenance including provider aliases", () => {
    const result = modelsFromManifest({
      runtime: {
        quick_think_llm: "legacy-fast",
        deep_think_llm: "legacy-reasoning",
        quick_provider: "deepseek",
        deep_think_provider: "anthropic",
        api_key: "private-secret-marker",
      },
    });
    expect(result.fallbackByTier).toBe(true);
    expect(result.roles.quick).toEqual({
      provider: "deepseek",
      model: "legacy-fast",
      source: "legacy",
    });
    expect(result.roles.deep).toEqual({
      provider: "anthropic",
      model: "legacy-reasoning",
      source: "legacy",
    });
    expect(JSON.stringify(result)).not.toContain("private-secret-marker");
  });

  it.each([undefined, null, "bad", [], {}, { runtime: [] }])(
    "keeps unavailable manifests unknown: %j",
    (manifest) => {
      expect(modelsFromManifest(manifest)).toEqual({
        label: "历史模型信息未确认",
        fallbackByTier: false,
        roles: {},
      });
    },
  );

  it("marks bootstrap defaults as current and untested, separate from frozen history", () => {
    const result = modelsFromBootstrap({
      configured: true,
      status: "configured",
      provider: "openai",
      quick_provider: "deepseek",
      deep_provider: "anthropic",
      quick_model: "current-fast",
      deep_model: "current-deep",
    });
    expect(result.roles.quick).toEqual({
      provider: "deepseek",
      model: "current-fast",
      source: "bootstrap",
      testStatus: "not_checked",
    });
    expect(result.roles.deep?.model).toBe("current-deep");
    expect(result.fallbackByTier).toBe(true);
    expect(modelsFromManifest(undefined).roles).toEqual({});
  });
});

type ObservedRun = Pick<RunSnapshot, "status" | "stage" | "reports">;
function observation(
  run?: ObservedRun,
  overrides: Partial<Parameters<typeof workflowNodeStatus>[0]> = {},
) {
  return workflowNodeStatus({
    roleId: "bull",
    stage: "debate",
    reportKeys: ["bull_history"],
    selectedAnalysts: ["market"],
    ...(run ? { run } : {}),
    ...overrides,
  });
}

describe("workflow evidence without invented per-role execution", () => {
  it("skips unselected analysts even if an inconsistent historical report exists", () => {
    expect(
      observation(
        { status: "completed", stage: "completed", reports: { news_report: "old" } },
        {
          roleId: "news",
          stage: "analysts",
          reportKeys: ["news_report"],
        },
      ).kind,
    ).toBe("skipped");
  });

  it.each(["failed", "cancelled", "interrupted", "completed"] as const)(
    "retains produced reports when the run is %s",
    (status) => {
      expect(
        observation({ status, stage: "risk", reports: { bull_history: "first round only" } }),
      ).toEqual({ kind: "output", label: "已有产出" });
    },
  );

  it("does not turn a completed run with missing or empty reports into green roles", () => {
    expect(
      observation({ status: "completed", stage: "completed", reports: { bull_history: "  " } })
        .kind,
    ).toBe("unknown");
    expect(
      observation(
        { status: "completed", stage: "completed" },
        { roleId: "archive", stage: "postprocess", reportKeys: [] },
      ).kind,
    ).toBe("unknown");
  });

  it.each([
    ["analysts", "analysts"],
    ["research", "debate"],
    ["research", "research_plan"],
    ["trading", "proposal"],
    ["risk", "risk_debate"],
    ["portfolio", "final_decision"],
  ])("shows only the coarse %s phase for workflow stage %s", (engineStage, stage) => {
    expect(observation({ status: "running", stage: engineStage }, { stage })).toEqual({
      kind: "current",
      label: "所处阶段",
    });
  });

  it("does not infer an individual role has completed merely because its phase passed", () => {
    expect(observation({ status: "running", stage: "portfolio" }).kind).toBe("unknown");
    expect(observation({ status: "running", stage: "analysts" }).kind).toBe("pending");
    expect(observation({ status: "running", stage: "unrecognized" }).kind).toBe("unknown");
    expect(
      observation({ status: "running", stage: "portfolio" }, { stage: "unrecognized" }).kind,
    ).toBe("unknown");
  });

  it("labels memory notes as diagnostics, including failures, without asserting memory success", () => {
    expect(
      observation(
        {
          status: "failed",
          stage: "analysts",
          reports: { memory_note: "Could not settle past decisions" },
        },
        {
          roleId: "memory_log",
          stage: "analysts",
          reportKeys: ["memory_note"],
        },
      ),
    ).toEqual({ kind: "output", label: "有诊断记录" });
  });

  it("distinguishes a plan, a queue and terminal missing evidence", () => {
    expect(observation().kind).toBe("pending");
    expect(observation({ status: "queued", stage: "queued" }).kind).toBe("pending");
    expect(observation({ status: "interrupted", stage: "research" }).kind).toBe("unknown");
    expect(observation({ status: "cancelling", stage: "research" }).kind).toBe("current");
  });

  it.each([
    [1, 1],
    [2, 2],
    [3, 3],
    [0, 1],
    [4, 3],
    [1.9, 1],
    [undefined, 1],
    ["2", 1],
    [NaN, 1],
    [Infinity, 1],
  ])("bounds configured depth %s to %s without claiming actual turn counts", (input, expected) =>
    expect(workflowDepth(input)).toBe(expected),
  );
});
