import { describe, expect, it } from "vitest";
import type {
  ConnectionView,
  ManifestResponse,
  ProfileView,
} from "../../shared/api/generated/models";
import {
  compatibleRuntimeProfiles,
  isRuntimeResearch,
  runtimeConnectionBody,
  runtimeManifestSummary,
  runtimeReady,
  runtimeUpdateBody,
} from "./runtime-state";
import type {
  RuntimeConnection,
  RuntimeEditorFields,
  RuntimeResearchRequest,
} from "./runtime-state";

const connection: RuntimeConnection = {
  id: "runtime",
  name: "本机研究",
  runtime_type: "codex",
  transport: "stdio",
  execution_host: "server_local",
  executable_path: null,
  model_id: "available-model",
  reasoning_effort: null,
  auth_mode: "runtime_login",
  model_profile_id: null,
  enabled: true,
  revision: 3,
  readiness: "ready",
  program_version: "0.164.0",
  auth_status: "authorized",
  login_command: null,
  capabilities: { dynamic_tools: true, cancel: true, resume: false, model_discovery: true },
  models: [],
  message: "可用",
  checked_at: null,
  created_at: "2026-10-28T00:00:00Z",
  updated_at: "2026-10-28T00:00:00Z",
};

describe("runtime execution eligibility", () => {
  it("requires an enabled checked and authorized runtime with tools and cancellation", () => {
    expect(runtimeReady(connection)).toBe(true);
    expect(runtimeReady(undefined)).toBe(false);
    expect(runtimeReady({ ...connection, enabled: false })).toBe(false);
    for (const readiness of ["not_checked", "failed", "stale"] as const)
      expect(runtimeReady({ ...connection, readiness })).toBe(false);
    for (const auth_status of ["not_authorized", "unknown"] as const)
      expect(runtimeReady({ ...connection, auth_status })).toBe(false);
    expect(
      runtimeReady({
        ...connection,
        capabilities: { ...connection.capabilities, dynamic_tools: false },
      }),
    ).toBe(false);
    expect(
      runtimeReady({ ...connection, capabilities: { ...connection.capabilities, cancel: false } }),
    ).toBe(false);
  });
  it("only offers tested profiles on enabled Responses connections with explicit non-OAuth auth", () => {
    const profile = (id: string, connection_id: string, test_status = "passed"): ProfileView => ({
      id,
      connection_id,
      connection_name: "离线连接",
      model_id: "fixture-model",
      provider: "custom",
      revision: 1,
      created_at: "2026-10-28T00:00:00Z",
      updated_at: "2026-10-28T00:00:00Z",
      tested_at: "2026-10-28T00:00:00Z",
      test_status,
      parameters: {},
      capabilities: { chat: true, tools: "supported" },
    });
    const api = (id: string, values: Partial<ConnectionView> = {}) =>
      ({
        id,
        enabled: true,
        auth_type: "api_key",
        resolved_protocol: "openai_responses",
        ...values,
      }) as ConnectionView;
    const profiles = [
      profile("allowed", "a"),
      profile("wrong-protocol", "b"),
      profile("disabled", "c"),
      profile("host-oauth", "d"),
      profile("stale", "a", "stale"),
      profile("missing", "absent"),
      { ...profile("unsupported-parameters", "a"), parameters: { temperature: 0.2 } },
      { ...profile("chat-only", "a"), capabilities: { chat: true, tools: "unsupported" } },
    ];
    const connections = [
      api("a"),
      api("b", { resolved_protocol: "openai_chat_completions" }),
      api("c", { enabled: false }),
      api("d", { auth_type: "oauth_local" }),
    ];
    expect(compatibleRuntimeProfiles(profiles, connections).map((item) => item.id)).toEqual([
      "allowed",
    ]);
  });
});

describe("runtime configuration requests", () => {
  const fields: RuntimeEditorFields = {
    name: " 研究 ",
    executable_path: " ",
    model_id: " model/id ",
    auth_mode: "runtime_login",
    model_profile_id: "stale-profile",
    enabled: false,
  };
  it("removes the stale API binding when using Runtime login and normalizes blank optional fields", () => {
    expect(runtimeConnectionBody(fields)).toEqual({
      name: "研究",
      runtime_type: "codex",
      executable_path: null,
      model_id: "model/id",
      reasoning_effort: null,
      auth_mode: "runtime_login",
      model_profile_id: null,
      enabled: false,
    });
  });
  it("retains the current account model reference without adding credential data", () => {
    const body = runtimeConnectionBody({
      ...fields,
      auth_mode: "model_connection",
      model_profile_id: "own-profile",
      reasoning_effort: "high",
    });
    expect(body).toMatchObject({
      auth_mode: "model_connection",
      model_profile_id: "own-profile",
      reasoning_effort: "high",
    });
    expect(Object.keys(body)).not.toContain("api_key");
  });
  it("sends compare-and-set revision on updates without the create-only Runtime type", () => {
    const body = runtimeUpdateBody(fields, 7);
    expect(body.revision).toBe(7);
    expect(body).not.toHaveProperty("runtime_type");
    expect(body.model_profile_id).toBeNull();
  });
});

describe("runtime historical identity", () => {
  const manifest = (
    selection: Record<string, unknown>,
    request: Partial<RuntimeResearchRequest> = {},
  ): ManifestResponse => ({
    selection,
    request: { ticker: "NVDA", date: "2026-10-28", ...request },
    schema_version: "1",
    graph_contract_version: "1",
    workspace_id: "w",
    owner_id: "o",
    asset_type: "stock",
    memory_scope: "run",
    runtime: {},
  });
  it("does not infer local Agent execution from unrelated Runtime fields or a model provider", () => {
    expect(isRuntimeResearch(undefined)).toBe(false);
    expect(isRuntimeResearch(manifest({ mode: "local", provider: "chatgpt" }))).toBe(false);
    expect(isRuntimeResearch(manifest({ execution_mode: "local_agent" }))).toBe(true);
    expect(isRuntimeResearch(manifest({}, { execution_mode: "local_agent" }))).toBe(true);
  });
  it("uses only supplied frozen metadata and leaves unknown history unknown", () => {
    expect(runtimeManifestSummary(undefined)).toEqual({
      name: undefined,
      runtime: undefined,
      model: undefined,
      transport: undefined,
      authMode: undefined,
      version: undefined,
    });
    const source = manifest({
      runtime_snapshot: {
        name: "旧连接",
        runtime_type: "codex",
        model_id: "frozen-model",
        transport: "stdio",
        auth_mode: "runtime_login",
        program_version: "old-version",
      },
    });
    expect(runtimeManifestSummary(source)).toEqual({
      name: "旧连接",
      runtime: "codex",
      model: "frozen-model",
      transport: "stdio",
      authMode: "runtime_login",
      version: "old-version",
    });
  });
  it("does not stringify malformed or nested Runtime metadata into a displayed identity", () => {
    const source = manifest({
      runtime_snapshot: {
        name: { secret: "hidden" },
        model_id: ["unconfirmed"],
        program_version: 123,
      },
    });
    expect(runtimeManifestSummary(source).name).toBeUndefined();
    expect(runtimeManifestSummary(source).model).toBeUndefined();
    expect(runtimeManifestSummary(source).version).toBeUndefined();
  });
});
