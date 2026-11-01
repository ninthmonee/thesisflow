import {
  cancelResearchRun,
  compareResearchRuns,
  createResearchContext,
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
import type {
  BootstrapResponse,
  ConnectionView,
  CreateResearchRequest,
  ModelSettings,
  ProfileView,
  ResearchContextCreate,
  RunSnapshot,
  RuntimeConnectionView,
} from "../../shared/api/generated/models";
import {
  assertCurrentSession,
  authGeneration,
  authWorkspaceId,
  notifySessionExpired,
} from "../../shared/api/session-state";
import { ApiError } from "../../shared/api/transport";
import { modelReadiness } from "../models/model-readiness";
import { runtimeReady } from "../runtimes/runtime-state";

const stageNumbers: Readonly<Record<string, number>> = {
  initializing: 0,
  queued: 0,
  analysts: 1,
  research: 2,
  trading: 3,
  risk: 4,
  portfolio: 5,
  runtime_running: 1,
  runtime_validating: 5,
  completed: 6,
};

const objectives: Readonly<Record<string, string>> = {
  general: "综合研究",
  technical: "技术观察",
  earnings: "财报研究",
  thematic: "主题研究",
  risk_review: "风险复核",
};

export function designRun(run: RunSnapshot) {
  return {
    id: run.id,
    symbol: run.ticker,
    date: run.date,
    objective: objectives[run.objective ?? "general"] ?? run.objective ?? "综合研究",
    mode: run.execution_mode === "local_agent" ? "local" : "builtin",
    depth: run.depth === 1 ? "quick" : run.depth === 3 ? "deep" : "standard",
    language: run.language,
    horizon: run.horizon ?? "medium",
    analystMarket: run.analysts.includes("market"),
    analystFundamentals: run.analysts.includes("fundamentals"),
    analystNews: run.analysts.includes("news"),
    analystSocial: run.analysts.includes("social"),
    maxCalls: run.max_llm_calls == null ? "80" : String(run.max_llm_calls),
    maxSeconds: run.max_runtime_seconds == null ? "1800" : String(run.max_runtime_seconds),
    runtimeId: run.runtime_connection_id ?? null,
    status: run.status,
    stage: stageNumbers[run.stage] ?? (run.status === "completed" ? 6 : 0),
    stageLabel: run.stage,
    created: run.created_at,
    template: run.execution_mode === "local_agent" ? "本地 Agent" : "内置 12 角色",
    sources: [],
    policy: false,
    quality: run.quality ?? null,
    rating: run.quality?.eligible_for_rating ? (run.rating ?? null) : null,
    rawRating: run.raw_rating ?? null,
    revision: run.revision,
    error: run.error ?? null,
    stats: run.stats ?? null,
    runtime: run.runtime_execution ?? null,
  };
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function string(value: unknown, fallback = "") {
  return typeof value === "string" ? value.trim() : fallback;
}

function invalid(message: string): never {
  throw new ApiError({ status: 400, code: "invalid_research_input", message });
}

function errorText(reason: unknown) {
  return reason instanceof ApiError ? reason.message : "暂时无法读取该项，请稍后重试。";
}

function boundedInteger(value: unknown, minimum: number, maximum: number, fallback: number) {
  const number = value === undefined || value === "" ? fallback : Number(value);
  if (!Number.isInteger(number) || number < minimum || number > maximum)
    invalid(`执行预算必须为 ${minimum}–${maximum} 范围内的整数。`);
  return number;
}

function runId(payload: unknown) {
  const data = record(payload);
  const id = typeof payload === "string" ? payload : string(data.id ?? data.runId);
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id))
    invalid("研究记录 ID 无效，请重新打开该记录。");
  return id;
}

/** Authenticated text download; apiFetch intentionally accepts JSON responses only. */
async function markdownReport(id: string) {
  const generation = authGeneration();
  const headers = new Headers({ Accept: "text/markdown", "X-Request-ID": crypto.randomUUID() });
  const workspace = authWorkspaceId();
  if (workspace) headers.set("X-Workspace-ID", workspace);
  let response: Response;
  try {
    response = await fetch(getGetResearchMarkdownReportUrl(id), {
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    assertCurrentSession(generation);
    throw new ApiError({ status: 0, code: "network_error", message: "无法连接本机服务。" });
  }
  const text = await response.text();
  assertCurrentSession(generation);
  if (response.status === 401) notifySessionExpired(generation);
  if (!response.ok) {
    let message = `无法导出报告（HTTP ${response.status}）。`;
    try {
      const body = record(JSON.parse(text) as unknown);
      const detail = record(body.detail);
      message = string(detail.message, string(body.detail, message));
    } catch {
      // Never expose a raw server error document as report content.
    }
    throw new ApiError({ status: response.status, code: "report_unavailable", message });
  }
  if (!text.trim()) invalid("该研究还没有可导出的报告。");
  return text;
}

/** Each authenticated frame owns a service instance; no private data survives account changes. */
export function createResearchService() {
  let bootstrap: BootstrapResponse | undefined;
  let settings: ModelSettings | undefined;
  let profiles: ProfileView[] = [];
  let connections: ConnectionView[] = [];
  let runtimes: RuntimeConnectionView[] = [];
  const runs = new Map<string, RunSnapshot>();
  const questions = new Map<string, string>();
  let idempotency: { fingerprint: string; key: string } | undefined;

  async function runCards(items: RunSnapshot[]) {
    // Questions are immutable manifest inputs; cache them per signed-in workspace.
    // Limit concurrent local reads when a library contains many records.
    const missing = items.filter((item) => !questions.has(item.id));
    for (let offset = 0; offset < missing.length; offset += 8) {
      await Promise.all(
        missing.slice(offset, offset + 8).map(async (item) => {
          try {
            const manifest = await getResearchManifest(item.id);
            questions.set(item.id, manifest?.request.research_instructions?.trim() || "");
          } catch {
            questions.set(item.id, "");
          }
        }),
      );
    }
    return items.map((item) => ({
      ...designRun(item),
      objective: questions.get(item.id) || designRun(item).objective,
    }));
  }

  async function load() {
    const results = await Promise.allSettled([
      listResearchRuns({ limit: 100 }),
      getBootstrap(),
      getModelSettings(),
      listModelProfiles(),
      listModelConnections(),
      listRuntimeConnections(),
    ]);
    const [
      runResult,
      bootstrapResult,
      settingsResult,
      profilesResult,
      connectionsResult,
      runtimesResult,
    ] = results;
    const errors: Record<string, string> = {};
    if (runResult.status === "rejected") throw runResult.reason;
    for (const item of runResult.value.items) runs.set(item.id, item);
    if (bootstrapResult.status === "fulfilled") bootstrap = bootstrapResult.value;
    else errors.bootstrap = errorText(bootstrapResult.reason);
    if (settingsResult.status === "fulfilled") settings = settingsResult.value;
    else errors.settings = errorText(settingsResult.reason);
    if (profilesResult.status === "fulfilled") profiles = profilesResult.value.profiles;
    else errors.profiles = errorText(profilesResult.reason);
    if (connectionsResult.status === "fulfilled") connections = connectionsResult.value.connections;
    else errors.connections = errorText(connectionsResult.reason);
    if (runtimesResult.status === "fulfilled") runtimes = runtimesResult.value.connections;
    else errors.runtimes = errorText(runtimesResult.reason);
    const template = settings?.default_template;
    const profile = profiles.find((item) => item.id === template?.default_profile_id);
    const readyRuntime = runtimes.find(runtimeReady);
    const model = {
      name: template?.name ?? (bootstrap?.model.configured ? "本机研究配置" : "尚未配置模型"),
      quick: profile?.model_id ?? bootstrap?.model.quick_model ?? "未配置",
      deep: profile?.model_id ?? bootstrap?.model.deep_model ?? "未配置",
      runtime: readyRuntime?.name ?? "尚无可用运行环境",
      runtimeId: readyRuntime?.id ?? null,
      templateId: template?.id ?? null,
      runtimes: runtimes.map((item) => ({
        id: item.id,
        name: item.name,
        model: item.model_id,
        ready: runtimeReady(item),
      })),
    };
    const readiness = template
      ? modelReadiness({
          defaultId: template.default_profile_id,
          bindings: template.role_bindings,
          analysts: template.analysts,
          roles: settings?.roles ?? [],
          profiles,
          connections,
        })
      : {
          ready: bootstrap?.model.configured === true,
          issues: bootstrap?.model.configured ? [] : ["请先在设置中心配置研究模型。"],
        };
    return {
      runs: await runCards(runResult.value.items),
      nextCursor: runResult.value.next_cursor ?? null,
      model,
      today:
        bootstrap?.today ??
        new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date()),
      readiness: { builtin: readiness.ready, local: !!readyRuntime, issues: readiness.issues },
      defaults: bootstrap?.defaults ?? {},
      errors,
    };
  }

  async function create(payload: unknown) {
    if (!bootstrap || !settings) await load();
    const draft = record(payload);
    const symbol = string(draft.symbol).toUpperCase();
    if (!symbol || symbol.length > 32) invalid("请填写有效的研究标的。");
    const date = string(draft.date);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || (bootstrap?.today && date > bootstrap.today))
      invalid("请选择不晚于当前美东日期的有效分析日期。");
    const objective = string(draft.objective);
    if (!objective) invalid("请先填写最想验证的研究问题。");
    const instructions = [objective, string(draft.instructions)]
      .filter(Boolean)
      .join("\n\n补充要求：\n");
    if (instructions.length > 4000) invalid("研究问题与补充要求合计不能超过 4000 字。");
    const selected: CreateResearchRequest["analysts"] = [];
    if (draft.analystMarket === true) selected.push("market");
    if (draft.analystFundamentals === true) selected.push("fundamentals");
    if (draft.analystNews === true) selected.push("news");
    if (draft.analystSocial === true) selected.push("social");
    const context = Array.isArray(draft.context) ? draft.context : [];
    if (
      context.length > 5 ||
      context.some(
        (id) =>
          typeof id !== "string" ||
          !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id),
      )
    )
      invalid("补充资料必须是已保存的真实资料快照，请先在市场发现中选择并附加。");
    const template = settings?.default_template;
    const local = draft.mode === "local";
    const requestedRuntime = string(draft.runtimeId ?? draft.runtimeConnectionId);
    const runtime = requestedRuntime
      ? runtimes.find((item) => item.id === requestedRuntime)
      : runtimes.find(runtimeReady);
    if (local && !runtimeReady(runtime)) invalid("尚无可用的本地 Agent，请先配置并检测运行环境。");
    if (!local && !selected.length) invalid("请至少启用一类分析师。");
    if (!local && !template && !bootstrap?.model.configured) invalid("请先配置研究模型。");
    if (!local && template) {
      const ready = modelReadiness({
        defaultId: template.default_profile_id,
        bindings: template.role_bindings,
        analysts: selected,
        roles: settings?.roles ?? [],
        profiles,
        connections,
      });
      if (!ready.ready) invalid(ready.issues.join("；"));
    }
    const body: CreateResearchRequest = {
      ticker: symbol,
      date,
      objective: "general",
      horizon: draft.horizon === "short" || draft.horizon === "medium" ? draft.horizon : "long",
      language: draft.language === "English" ? "English" : "Chinese",
      depth: draft.depth === "quick" ? 1 : draft.depth === "deep" ? 3 : 2,
      analysts: selected.length ? selected : ["market", "fundamentals", "news", "social"],
      execution_mode: local ? "local_agent" : "builtin_workflow",
      runtime_connection_id: local ? (runtime?.id ?? null) : null,
      template_version_id: local ? null : (template?.id ?? null),
      checkpoint: false,
      apply_investment_system: draft.policy === true,
      investment_plan_id: draft.policy === true ? string(draft.planId) || null : null,
      research_instructions: instructions,
      max_llm_calls: boundedInteger(draft.maxCalls, 1, 500, 80),
      max_runtime_seconds: boundedInteger(draft.maxSeconds, 60, 14400, 1800),
      context_ids: [...new Set(context as string[])],
    };
    const fingerprint = JSON.stringify(body);
    if (idempotency?.fingerprint !== fingerprint)
      idempotency = { fingerprint, key: crypto.randomUUID() };
    const result = await createResearchRun(body, {
      headers: { "Idempotency-Key": idempotency.key },
    });
    idempotency = undefined;
    runs.set(result.id, result);
    return designRun(result);
  }

  return async (method: string, payload: unknown): Promise<unknown> => {
    switch (method) {
      case "research.load":
        return load();
      case "research.create":
        return create(payload);
      case "research.detail": {
        const id = runId(payload);
        const snapshot = await getResearchRun(id);
        runs.set(id, snapshot);
        const [evidence, manifest, viewpoints] = await Promise.allSettled([
          getResearchEvidence(id, { limit: 100 }),
          getResearchManifest(id),
          listViewpoints({ run_id: id }),
        ]);
        const errors: Record<string, string> = {};
        if (evidence.status === "rejected") errors.evidence = errorText(evidence.reason);
        if (manifest.status === "rejected") errors.manifest = errorText(manifest.reason);
        if (viewpoints.status === "rejected") errors.viewpoints = errorText(viewpoints.reason);
        const mapped = designRun(snapshot);
        return {
          run: {
            ...mapped,
            objective:
              manifest.status === "fulfilled"
                ? manifest.value.request.research_instructions || mapped.objective
                : mapped.objective,
            // The API stores question and supplemental text together. Keep that full
            // frozen text in objective and leave instructions empty to avoid duplication.
            instructions: "",
            horizon:
              manifest.status === "fulfilled"
                ? (manifest.value.request.horizon ?? mapped.horizon)
                : mapped.horizon,
            runtimeId:
              manifest.status === "fulfilled"
                ? (manifest.value.request.runtime_connection_id ?? mapped.runtimeId)
                : mapped.runtimeId,
            policy:
              manifest.status === "fulfilled" &&
              manifest.value.request.apply_investment_system === true,
            sources:
              manifest.status === "fulfilled" ? (manifest.value.request.context_ids ?? []) : [],
            planId:
              manifest.status === "fulfilled"
                ? (manifest.value.request.investment_plan_id ?? "")
                : "",
          },
          snapshot,
          evidence: evidence.status === "fulfilled" ? evidence.value : null,
          manifest: manifest.status === "fulfilled" ? manifest.value : null,
          viewpoints: viewpoints.status === "fulfilled" ? viewpoints.value : null,
          errors,
        };
      }
      case "research.cancel": {
        const id = runId(payload);
        const result = await cancelResearchRun(id);
        runs.set(result.id, result);
        return designRun(result);
      }
      case "research.more": {
        const cursor = string(record(payload).cursor);
        if (!cursor) invalid("缺少后续记录游标，请刷新研究库。");
        const result = await listResearchRuns({ limit: 100, cursor });
        for (const item of result.items) runs.set(item.id, item);
        return { runs: await runCards(result.items), nextCursor: result.next_cursor ?? null };
      }
      case "research.export": {
        const id = runId(payload);
        const markdown = await markdownReport(id);
        return { markdown, filename: `${runs.get(id)?.ticker ?? id}-研究报告.md` };
      }
      case "research.compare": {
        const data = record(payload);
        const ids = Array.isArray(payload) ? payload : data.ids;
        if (!Array.isArray(ids) || ids.length !== 2) invalid("请选择两份同一证券的已完成研究。");
        const first = runId(ids[0]);
        const second = runId(ids[1]);
        if (first === second) invalid("请选择两份不同的研究记录。");
        return compareResearchRuns(first, { other_run_id: second });
      }
      case "research.context": {
        const data = record(payload);
        const kind = string(data.kind);
        if (!["market", "theme", "options", "news", "etf_flows"].includes(kind))
          invalid("资料类型无效。");
        const body: ResearchContextCreate = {
          kind: kind as ResearchContextCreate["kind"],
          symbol: string(data.symbol) || null,
          market_snapshot_id: string(data.market_snapshot_id ?? data.marketSnapshotId) || null,
          theme_id: string(data.theme_id ?? data.themeId) || null,
          theme_version:
            typeof data.theme_version === "number"
              ? data.theme_version
              : typeof data.themeVersion === "number"
                ? data.themeVersion
                : null,
        };
        return createResearchContext(body);
      }
      default:
        return undefined;
    }
  };
}
