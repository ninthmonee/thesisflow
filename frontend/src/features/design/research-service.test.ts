import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createResearchRun,
  getBootstrap,
  getGetResearchMarkdownReportUrl,
  getModelSettings,
  getResearchEvidence,
  getResearchManifest,
  getResearchRun,
  listModelConnections,
  listModelProfiles,
  listResearchRuns,
  listRuntimeConnections,
  listViewpoints,
} from "../../shared/api/generated/client";
import type { BootstrapResponse, RunSnapshot } from "../../shared/api/generated/models";
import { clearAuthSession, installAuthSession } from "../../shared/api/session-state";
import { ApiError } from "../../shared/api/transport";
import { createResearchService, designRun } from "./research-service";

type ResearchClient = typeof import("../../shared/api/generated/client");

vi.mock("../../shared/api/generated/client", () => ({
  cancelResearchRun: vi.fn<ResearchClient["cancelResearchRun"]>(),
  compareResearchRuns: vi.fn<ResearchClient["compareResearchRuns"]>(),
  createResearchContext: vi.fn<ResearchClient["createResearchContext"]>(),
  createResearchRun: vi.fn<ResearchClient["createResearchRun"]>(),
  getBootstrap: vi.fn<ResearchClient["getBootstrap"]>(),
  getGetResearchMarkdownReportUrl: vi.fn<ResearchClient["getGetResearchMarkdownReportUrl"]>(),
  getModelSettings: vi.fn<ResearchClient["getModelSettings"]>(),
  getResearchEvidence: vi.fn<ResearchClient["getResearchEvidence"]>(),
  getResearchManifest: vi.fn<ResearchClient["getResearchManifest"]>(),
  getResearchRun: vi.fn<ResearchClient["getResearchRun"]>(),
  listModelConnections: vi.fn<ResearchClient["listModelConnections"]>(),
  listModelProfiles: vi.fn<ResearchClient["listModelProfiles"]>(),
  listResearchRuns: vi.fn<ResearchClient["listResearchRuns"]>(),
  listRuntimeConnections: vi.fn<ResearchClient["listRuntimeConnections"]>(),
  listViewpoints: vi.fn<ResearchClient["listViewpoints"]>(),
}));

const id = "11223344-5566-4788-99aa-bbccddeeff00";
const contextId = "99887766-5544-4322-aabb-ccddeeff0011";

function snapshot(patch: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    id,
    ticker: "NVDA",
    date: "2026-10-30",
    analysts: ["market", "fundamentals"],
    asset_type: "stock",
    checkpoint: false,
    created_at: "2026-10-30T08:05:00Z",
    depth: 2,
    execution_mode: "builtin_workflow",
    language: "Chinese",
    objective: "general",
    revision: 3,
    stage: "completed",
    status: "completed",
    reports: { final_trade_decision: "真实保存的研究结论" },
    ...patch,
  };
}

function bootstrap(configured = true): BootstrapResponse {
  return {
    application: { version: "test" },
    capabilities: [],
    defaults: { analysts: ["market", "fundamentals"], depth: 2, language: "Chinese" },
    model: {
      configured,
      status: configured ? "configured" : "missing",
      provider: "configured-provider",
      quick_provider: "configured-provider",
      deep_provider: "configured-provider",
      quick_model: configured ? "actual-quick" : null,
      deep_model: configured ? "actual-deep" : null,
    },
    services: [],
    today: "2026-10-30",
    workspace: { id: "private-workspace", name: "真实工作区", owner_id: "owner" },
  };
}

function draft() {
  return {
    symbol: "nvda",
    objective: "核实增长是否转化为现金流",
    instructions: "比较最近两个季度的证据",
    date: "2026-10-30",
    horizon: "long",
    mode: "builtin",
    depth: "deep",
    language: "English",
    analystMarket: true,
    analystFundamentals: true,
    analystNews: false,
    analystSocial: false,
    policy: true,
    planId: "selected-plan",
    context: [contextId],
    maxCalls: "37",
    maxSeconds: "900",
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  installAuthSession({
    userId: "owner",
    workspaceId: "private-workspace",
    csrfToken: "memory-token",
  });
  vi.mocked(listResearchRuns).mockResolvedValue({ items: [snapshot()] });
  vi.mocked(getBootstrap).mockResolvedValue(bootstrap());
  vi.mocked(getModelSettings).mockResolvedValue({ default_template: null, roles: [] });
  vi.mocked(listModelProfiles).mockResolvedValue({ profiles: [] });
  vi.mocked(listModelConnections).mockResolvedValue({ connections: [] });
  vi.mocked(listRuntimeConnections).mockResolvedValue({ connections: [] });
  vi.mocked(createResearchRun).mockResolvedValue(snapshot({ status: "queued", stage: "queued" }));
  vi.mocked(getGetResearchMarkdownReportUrl).mockImplementation(
    (run) => `/api/v1/runs/${run}/report.md`,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearAuthSession();
});

it("keeps frozen research questions searchable after refreshing the library", async () => {
  vi.mocked(getResearchManifest).mockResolvedValue({
    asset_type: "stock",
    graph_contract_version: "test",
    memory_scope: "workspace",
    owner_id: "owner",
    workspace_id: "private-workspace",
    schema_version: "1",
    request: {
      ticker: "NVDA",
      date: "2026-10-30",
      research_instructions: "核实需求能否转化为持续现金流。",
    },
    runtime: {},
    selection: {},
  });
  const service = createResearchService();
  const first = await service("research.load", undefined);
  const second = await service("research.load", undefined);
  expect(first).toMatchObject({ runs: [{ objective: "核实需求能否转化为持续现金流。" }] });
  expect(second).toMatchObject({ runs: [{ objective: "核实需求能否转化为持续现金流。" }] });
  expect(getResearchManifest).toHaveBeenCalledTimes(1);
});

describe("authenticated design research service", () => {
  it.each([
    "pending",
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "failed",
    "interrupted",
    "completed",
  ] as const)("keeps the real %s state instead of manufacturing a completed report", (status) => {
    expect(
      designRun(
        snapshot({ status, stage: "analysts", error: status === "failed" ? "取证失败" : null }),
      ),
    ).toMatchObject({
      status,
      stage: 1,
      stageLabel: "analysts",
      rating: null,
      error: status === "failed" ? "取证失败" : null,
    });
  });

  it("reads saved records even when the runtime capability endpoint is unavailable", async () => {
    vi.mocked(listRuntimeConnections).mockRejectedValue(
      new ApiError({ status: 503, code: "unavailable", message: "运行环境不可读取" }),
    );
    const service = createResearchService();
    await expect(service("research.load", {})).resolves.toMatchObject({
      runs: [{ id, status: "completed" }],
      model: { quick: "actual-quick", deep: "actual-deep", runtimeId: null },
      today: "2026-10-30",
      readiness: { builtin: true, local: false },
      errors: { runtimes: "运行环境不可读取" },
    });
    expect(createResearchRun).not.toHaveBeenCalled();
  });

  it("submits the reviewed question, instructions, context, discipline and budgets only on explicit create", async () => {
    const service = createResearchService();
    await service("research.load", {});
    expect(createResearchRun).not.toHaveBeenCalled();
    await service("research.create", draft());
    expect(createResearchRun).toHaveBeenCalledWith(
      {
        ticker: "NVDA",
        date: "2026-10-30",
        objective: "general",
        research_instructions: "核实增长是否转化为现金流\n\n补充要求：\n比较最近两个季度的证据",
        horizon: "long",
        language: "English",
        depth: 3,
        analysts: ["market", "fundamentals"],
        execution_mode: "builtin_workflow",
        runtime_connection_id: null,
        template_version_id: null,
        checkpoint: false,
        apply_investment_system: true,
        investment_plan_id: "selected-plan",
        max_llm_calls: 37,
        max_runtime_seconds: 900,
        context_ids: [contextId],
      },
      { headers: { "Idempotency-Key": expect.any(String) } },
    );
  });

  it("reuses the creation key after an uncertain response and rotates it after success", async () => {
    const service = createResearchService();
    await service("research.load", {});
    vi.mocked(createResearchRun).mockRejectedValueOnce(
      new ApiError({ status: 0, code: "network_error", message: "连接中断" }),
    );
    await expect(service("research.create", draft())).rejects.toMatchObject({
      code: "network_error",
    });
    await service("research.create", draft());
    await service("research.create", { ...draft(), objective: "另一个明确问题" });
    const keys = vi
      .mocked(createResearchRun)
      .mock.calls.map((call) => new Headers(call[1]?.headers).get("Idempotency-Key"));
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("blocks an unconfigured account before creating a paid research task", async () => {
    vi.mocked(getBootstrap).mockResolvedValue(bootstrap(false));
    const service = createResearchService();
    await expect(service("research.create", draft())).rejects.toMatchObject({
      message: "请先配置研究模型。",
    });
    expect(createResearchRun).not.toHaveBeenCalled();
  });

  it("rejects prototype context labels instead of attaching fabricated evidence", async () => {
    const service = createResearchService();
    await expect(
      service("research.create", { ...draft(), context: ["AI 算力主题"] }),
    ).rejects.toMatchObject({ code: "invalid_research_input" });
    expect(createResearchRun).not.toHaveBeenCalled();
  });

  it("does not silently run a local-agent request through the built-in workflow", async () => {
    const service = createResearchService();
    await expect(service("research.create", { ...draft(), mode: "local" })).rejects.toMatchObject({
      message: "尚无可用的本地 Agent，请先配置并检测运行环境。",
    });
    expect(createResearchRun).not.toHaveBeenCalled();
  });

  it("preserves saved report content when an evidence read fails independently", async () => {
    vi.mocked(getResearchRun).mockResolvedValue(
      snapshot({ max_llm_calls: 23, max_runtime_seconds: 600, horizon: "short" }),
    );
    vi.mocked(getResearchEvidence).mockRejectedValue(
      new ApiError({ status: 503, code: "unavailable", message: "证据暂不可读" }),
    );
    vi.mocked(getResearchManifest).mockResolvedValue({
      schema_version: "1",
      graph_contract_version: "1",
      asset_type: "stock",
      memory_scope: "run",
      owner_id: "owner",
      workspace_id: "private-workspace",
      runtime: {},
      selection: {},
      request: {
        ticker: "NVDA",
        date: "2026-10-30",
        research_instructions: "原始问题",
        apply_investment_system: true,
        context_ids: [contextId],
      },
    });
    vi.mocked(listViewpoints).mockResolvedValue({ items: [] });
    const service = createResearchService();
    await expect(service("research.detail", { id })).resolves.toMatchObject({
      run: {
        id,
        objective: "原始问题",
        instructions: "",
        horizon: "short",
        maxCalls: "23",
        maxSeconds: "600",
        analystMarket: true,
        analystFundamentals: true,
        analystNews: false,
        analystSocial: false,
        policy: true,
        sources: [contextId],
      },
      snapshot: { reports: { final_trade_decision: "真实保存的研究结论" } },
      evidence: null,
      errors: { evidence: "证据暂不可读" },
    });
  });

  it("exports the exact protected report bytes from the current workspace", async () => {
    const markdown = "# Saved report\n\nActual server content, with its evidence.\n";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(markdown, { headers: { "Content-Type": "text/markdown" } }));
    vi.stubGlobal("fetch", fetchMock);
    const service = createResearchService();
    await service("research.load", {});
    await expect(service("research.export", { id })).resolves.toEqual({
      markdown,
      filename: "NVDA-研究报告.md",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/runs/${id}/report.md`,
      expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-Workspace-ID")).toBe("private-workspace");
    expect(headers.get("Accept")).toBe("text/markdown");
  });

  it("discards a late private export after the account changes", async () => {
    let finish: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
    );
    const service = createResearchService();
    const exporting = service("research.export", { id });
    installAuthSession({
      userId: "different-owner",
      workspaceId: "different-workspace",
      csrfToken: "different-token",
    });
    finish?.(new Response("prior account's private report"));
    await expect(exporting).rejects.toMatchObject({ name: "AbortError" });
  });
});
