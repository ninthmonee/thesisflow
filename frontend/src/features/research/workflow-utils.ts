import type { ModelStatus, ProfileView, RunSnapshot } from "../../shared/api/generated/models";

export interface WorkflowModelAssignment {
  provider?: string;
  model?: string;
  connectionName?: string;
  source: string;
  testStatus?: string;
}

export interface WorkflowModelBindings {
  label: string;
  fallbackByTier: boolean;
  roles: Readonly<Record<string, WorkflowModelAssignment>>;
}

const roleIds = [
  "market",
  "social",
  "news",
  "fundamentals",
  "bull",
  "bear",
  "research_manager",
  "trader",
  "risk_aggressive",
  "risk_neutral",
  "risk_conservative",
  "portfolio_manager",
] as const;
const analystIds: readonly string[] = ["market", "social", "news", "fundamentals"];
const modelKeys = [...roleIds, "quick", "deep"] as const;

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function own(value: Record<string, unknown> | undefined, key: string): unknown {
  return value && Object.hasOwn(value, key) ? value[key] : undefined;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const clean = value.trim();
  if (!clean || clean.length > 240) return undefined;
  for (let index = 0; index < clean.length; index += 1) {
    const code = clean.charCodeAt(index);
    if (code < 32 || code === 127) return undefined;
  }
  return clean;
}

function assignment(
  value: Record<string, unknown> | undefined,
  source: string,
  modelKey = "model",
): WorkflowModelAssignment {
  const provider = text(own(value, "provider"));
  const model = text(own(value, modelKey));
  const connectionName = text(own(value, "connection_name"));
  const testStatus = text(own(value, "test_status"));
  return {
    ...(provider ? { provider } : {}),
    ...(model ? { model } : {}),
    ...(connectionName ? { connectionName } : {}),
    ...(testStatus ? { testStatus } : {}),
    source,
  };
}

export function modelsFromProfiles(
  template: { default_profile_id?: string; role_bindings?: Record<string, string> },
  profiles: ProfileView[],
  source: "draft" | "template",
): WorkflowModelBindings {
  const byId = new Map(profiles.map((profile) => [profile.id, profile]));
  const roles: Record<string, WorkflowModelAssignment> = {};
  const defaultProfile = template.default_profile_id
    ? byId.get(template.default_profile_id)
    : undefined;
  if (template.default_profile_id) {
    const model = assignment(record(defaultProfile), source, "model_id");
    roles.quick = model;
    roles.deep = model;
  }
  for (const role of roleIds) {
    const profileId = template.role_bindings?.[role];
    if (profileId) {
      // Keep a missing explicit binding unknown instead of silently inheriting another model.
      roles[role] = assignment(record(byId.get(profileId)), source, "model_id");
    }
  }
  return {
    label: source === "draft" ? "当前编辑的模型配置" : "模板模型配置",
    fallbackByTier: defaultProfile !== undefined,
    roles,
  };
}

export function modelsFromManifest(manifest: unknown): WorkflowModelBindings {
  const document = record(manifest);
  const selection = record(own(document, "selection"));
  if (own(selection, "execution_mode") === "local_agent") {
    const snapshot = record(own(selection, "runtime_snapshot"));
    const model = text(own(snapshot, "model_id"));
    return {
      label: `本地 Agent 独立研究${model ? ` · 请求模型 ${model}` : ""}，不使用内置角色模型绑定`,
      fallbackByTier: false,
      roles: {},
    };
  }
  if (selection && Object.hasOwn(selection, "model_snapshot")) {
    const snapshot = record(own(selection, "model_snapshot"));
    const frozen = record(own(snapshot, "roles"));
    const roles: Record<string, WorkflowModelAssignment> = {};
    for (const role of modelKeys) {
      if (frozen && Object.hasOwn(frozen, role)) {
        roles[role] = assignment(record(own(frozen, role)), "manifest");
      }
    }
    return { label: "本次研究冻结的模型配置", fallbackByTier: false, roles };
  }

  const runtime = record(own(document, "runtime"));
  const roles: Record<string, WorkflowModelAssignment> = {};
  for (const tier of ["quick", "deep"] as const) {
    const provider =
      text(own(runtime, `${tier}_think_provider`)) ??
      text(own(runtime, `${tier}_provider`)) ??
      text(own(runtime, "llm_provider"));
    const model = text(own(runtime, `${tier}_think_llm`));
    if (provider || model) {
      roles[tier] = {
        ...(provider ? { provider } : {}),
        ...(model ? { model } : {}),
        source: "legacy",
      };
    }
  }
  return {
    label: Object.keys(roles).length ? "历史研究的部署模型配置" : "历史模型信息未确认",
    fallbackByTier: Object.keys(roles).length > 0,
    roles,
  };
}

export function modelsFromBootstrap(model: ModelStatus): WorkflowModelBindings {
  const roles: Record<string, WorkflowModelAssignment> = {};
  for (const tier of ["quick", "deep"] as const) {
    const provider = text(model[`${tier}_provider`]) ?? text(model.provider);
    const name = text(model[`${tier}_model`]);
    if (provider || name) {
      roles[tier] = {
        ...(provider ? { provider } : {}),
        ...(name ? { model: name } : {}),
        source: "bootstrap",
        testStatus: "not_checked",
      };
    }
  }
  return { label: "当前部署的默认模型（未检测）", fallbackByTier: true, roles };
}

export function workflowDepth(value: unknown): 1 | 2 | 3 {
  if (typeof value !== "number" || !Number.isFinite(value)) return 1;
  return Math.max(1, Math.min(3, Math.trunc(value))) as 1 | 2 | 3;
}

export interface WorkflowNodeObservation {
  kind: "skipped" | "output" | "current" | "unknown" | "pending";
  label: string;
}

const stageGroups: readonly (readonly string[])[] = [
  ["initializing"],
  ["analysts"],
  ["research", "debate", "research_plan"],
  ["trading", "proposal"],
  ["risk", "risk_debate"],
  ["portfolio", "final_decision"],
  ["postprocess"],
];

export function workflowNodeStatus({
  roleId,
  stage,
  reportKeys,
  selectedAnalysts,
  run,
}: {
  roleId: string;
  stage: string;
  reportKeys: readonly string[];
  selectedAnalysts: readonly string[];
  run?: Pick<RunSnapshot, "status" | "stage" | "reports">;
}): WorkflowNodeObservation {
  if (analystIds.includes(roleId) && !selectedAnalysts.includes(roleId)) {
    return { kind: "skipped", label: "未选分析师" };
  }
  if (!run) return { kind: "pending", label: "计划执行" };
  if (
    reportKeys.some((key) => typeof run.reports?.[key] === "string" && run.reports[key]?.trim())
  ) {
    return {
      kind: "output",
      label: reportKeys.includes("memory_note") ? "有诊断记录" : "已有产出",
    };
  }
  if (["completed", "failed", "cancelled", "interrupted"].includes(run.status)) {
    return { kind: "unknown", label: "暂无产出证据" };
  }
  const currentStage = stageGroups.findIndex((group) => group.includes(run.stage));
  const nodeStage = stageGroups.findIndex((group) => group.includes(stage));
  if (
    (run.status === "running" || run.status === "cancelling") &&
    currentStage >= 0 &&
    currentStage === nodeStage
  ) {
    return { kind: "current", label: "所处阶段" };
  }
  if (
    run.status === "pending" ||
    run.status === "queued" ||
    (currentStage >= 0 && nodeStage >= 0 && nodeStage > currentStage)
  ) {
    return { kind: "pending", label: "等待对应阶段" };
  }
  return { kind: "unknown", label: "暂无逐角色状态" };
}
