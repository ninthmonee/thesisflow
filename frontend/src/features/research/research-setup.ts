import type {
  CreateResearchRequest,
  ManifestResponse,
  RunSnapshot,
  TemplateView,
} from "../../shared/api/generated/models";
import type { WorkflowModelBindings } from "./workflow-utils";

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
): Partial<CreateResearchRequest> {
  const original = manifest.request;
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

export function researchRequest(values: CreateResearchRequest): CreateResearchRequest {
  return {
    ...values,
    ticker: values.ticker.trim().toUpperCase(),
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
