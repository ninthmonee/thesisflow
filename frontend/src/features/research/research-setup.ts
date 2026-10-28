import type {
  ManifestResponse,
  RunSnapshot,
  TemplateView,
} from "../../shared/api/generated/models";
import type { WorkflowModelBindings } from "./workflow-utils";
import { isRuntimeResearch } from "../runtimes/runtime-state";
import type { RuntimeResearchRequest } from "../runtimes/runtime-state";

export const CURRENT_MODEL_CONFIGURATION = "__current_configuration__";
export type ResearchPrefillMode = "current" | "original";

export function researchPrefillUrl(runId: string, mode: ResearchPrefillMode) {
  return `/research/new?${new URLSearchParams({ from_run: runId, prefill: mode })}`;
}

export function sourceTemplateId(manifest: ManifestResponse): string | undefined {
  const explicit = manifest.request.template_version_id;
  if (explicit) return explicit;
  const snapshot = manifest.selection.model_snapshot;
  if (snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)) {
    const id = (snapshot as Record<string, unknown>).template_version_id;
    if (typeof id === "string" && id) return id;
  }
  return undefined;
}

export function researchPrefill(
  manifest: ManifestResponse,
  mode: ResearchPrefillMode,
  today: string,
  templates: readonly TemplateView[],
  currentTemplate?: TemplateView | null,
): Partial<RuntimeResearchRequest> {
  const original: RuntimeResearchRequest = {
    ...manifest.request,
    apply_investment_system: manifest.request.apply_investment_system ?? false,
    investment_plan_id: manifest.request.investment_plan_id ?? null,
  };
  if (isRuntimeResearch(manifest)) {
    return {
      ...original,
      execution_mode: "local_agent",
      date: mode === "current" ? today : original.date,
      context_ids: mode === "current" ? [] : (original.context_ids ?? []),
      checkpoint: false,
      template_version_id: null,
    };
  }
  if (mode === "current") {
    return {
      ...original,
      date: today,
      context_ids: [],
      template_version_id: currentTemplate?.id ?? CURRENT_MODEL_CONFIGURATION,
      ...(currentTemplate
        ? {
            analysts: currentTemplate.analysts,
            depth: currentTemplate.depth,
            language: currentTemplate.language,
          }
        : {}),
    };
  }
  const originalId = sourceTemplateId(manifest);
  return {
    ...original,
    // An unavailable historical binding must be chosen explicitly before submission.
    template_version_id: templates.some((template) => template.id === originalId)
      ? originalId
      : undefined,
  };
}

export function modelBindingSummary(models: WorkflowModelBindings) {
  const labels = [
    ...new Set(
      Object.values(models.roles).map((item) =>
        item.model ? `${item.provider ?? "厂商未知"} / ${item.model}` : "模型未知",
      ),
    ),
  ];
  return labels.length ? labels.join("；") : "模型信息未确认";
}

export function researchRequest(values: RuntimeResearchRequest): RuntimeResearchRequest {
  const prepared = {
    ...values,
    investment_plan_id: values.apply_investment_system ? (values.investment_plan_id ?? null) : null,
  };
  if (values.execution_mode === "local_agent") {
    return {
      ...prepared,
      ticker: values.ticker.trim().toUpperCase(),
      template_version_id: null,
      checkpoint: false,
      research_instructions: values.research_instructions?.trim() ?? "",
    };
  }
  return {
    ...prepared,
    ticker: values.ticker.trim().toUpperCase(),
    runtime_connection_id: null,
    template_version_id:
      !values.template_version_id || values.template_version_id === CURRENT_MODEL_CONFIGURATION
        ? null
        : values.template_version_id,
  };
}

export function researchDefaultTab(status: string, selected?: string) {
  return (
    selected ??
    (["completed", "failed", "cancelled", "interrupted"].includes(status)
      ? "conclusion"
      : "workflow")
  );
}

export function qualifiedResearchRating(run: Pick<RunSnapshot, "rating" | "quality">) {
  return run.quality?.eligible_for_rating === true ? (run.rating ?? undefined) : undefined;
}

export function researchContextIds(values: readonly string[]) {
  const raw = [...new Set(values.flatMap((value) => value.split(",")))].filter(Boolean);
  const valid = raw.filter((value) =>
    /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value),
  );
  return {
    ids: valid.slice(0, 5),
    warning:
      valid.length !== raw.length
        ? "链接中有无效资料ID，未附加该项。"
        : valid.length > 5
          ? "一次最多附加5项资料，超出项尚未选择。"
          : undefined,
  };
}
