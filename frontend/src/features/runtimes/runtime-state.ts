import type {
  ConnectionView,
  CreateResearchRequest,
  ManifestResponse,
  ProfileView,
  RuntimeConnectionCreate,
  RuntimeConnectionUpdate,
  RuntimeConnectionView,
} from "../../shared/api/generated/models";

export type RuntimeConnection = RuntimeConnectionView;

export interface RuntimeEditorFields {
  name: string;
  executable_path?: string | null;
  model_id: string;
  reasoning_effort?: RuntimeConnection["reasoning_effort"];
  auth_mode: "runtime_login" | "model_connection";
  model_profile_id?: string | null;
  enabled: boolean;
}

export type RuntimeResearchRequest = CreateResearchRequest;

export const runtimeReadinessLabels = {
  not_checked: "尚未检测",
  ready: "检测通过",
  failed: "检测失败",
  stale: "需要重新检测",
};

export function runtimeReady(connection: RuntimeConnection | undefined): boolean {
  return (
    !!connection?.enabled &&
    connection.readiness === "ready" &&
    connection.auth_status === "authorized" &&
    connection.capabilities?.dynamic_tools === true &&
    connection.capabilities.cancel === true
  );
}

export function compatibleRuntimeProfiles(
  profiles: readonly ProfileView[],
  connections: readonly ConnectionView[],
) {
  return profiles.filter((profile) => {
    const connection = connections.find((item) => item.id === profile.connection_id);
    return (
      profile.test_status === "passed" &&
      Object.keys(profile.parameters ?? {}).length === 0 &&
      (profile.capabilities?.tools === true || profile.capabilities?.tools === "supported") &&
      connection?.enabled &&
      connection.auth_type !== "oauth_local" &&
      connection.resolved_protocol === "openai_responses"
    );
  });
}

export function runtimeConnectionBody(fields: RuntimeEditorFields): RuntimeConnectionCreate {
  return {
    name: fields.name.trim(),
    runtime_type: "codex" as const,
    executable_path: fields.executable_path?.trim() || null,
    model_id: fields.model_id.trim(),
    reasoning_effort: fields.reasoning_effort || null,
    auth_mode: fields.auth_mode,
    model_profile_id:
      fields.auth_mode === "model_connection" ? fields.model_profile_id || null : null,
    enabled: fields.enabled,
  };
}

export function runtimeUpdateBody(
  fields: RuntimeEditorFields,
  revision: number,
): RuntimeConnectionUpdate {
  const { runtime_type: _runtimeType, ...body } = runtimeConnectionBody(fields);
  return { ...body, revision };
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function isRuntimeResearch(manifest: ManifestResponse | undefined): boolean {
  return (
    manifest?.selection.execution_mode === "local_agent" ||
    manifest?.request.execution_mode === "local_agent"
  );
}

export function runtimeManifestSummary(manifest: ManifestResponse | undefined) {
  const snapshot = record(manifest?.selection.runtime_snapshot);
  const value = (key: string) =>
    typeof snapshot?.[key] === "string" ? (snapshot[key] as string) : undefined;
  return {
    name: value("name"),
    runtime: value("runtime_type"),
    model: value("model_id"),
    transport: value("transport"),
    authMode: value("auth_mode"),
    version: value("program_version"),
  };
}
