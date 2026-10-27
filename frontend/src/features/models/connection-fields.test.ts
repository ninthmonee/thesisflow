import { describe, expect, it } from "vitest";
import { connectionFields, customHeaders } from "./connection-fields";

describe("custom model connection requests", () => {
  it("allowlists fields and leaves untouched secret values out", () => {
    const fields = connectionFields({
      name: " Gateway ",
      provider: "custom",
      vendor_name: " My vendor ",
      auth_type: "none",
      endpoint: "https://gateway.example/v1",
      api_protocol: "openai_responses",
      header_rows: [{ name: "", value: "" }],
    });
    expect(fields).toEqual({
      name: "Gateway",
      vendor_name: "My vendor",
      endpoint: "https://gateway.example/v1",
      api_protocol: "openai_responses",
      auth_type: "none",
      enabled: true,
    });
    expect(fields).not.toHaveProperty("header_rows");
    expect(fields).not.toHaveProperty("api_key");
  });
  it("supports header-only auth without a fabricated API key", () => {
    const fields = connectionFields({
      name: "Gateway",
      provider: "custom",
      auth_type: "headers",
      header_rows: [{ name: "Authorization", value: "Bearer PRIVATE" }],
    });
    expect(fields.extra_headers).toEqual({ Authorization: "Bearer PRIVATE" });
    expect(fields).not.toHaveProperty("api_key");
  });
  it.each([
    {
      rows: [
        { name: "X-Auth", value: "PRIVATE" },
        { name: "x-auth", value: "OTHER" },
      ],
    },
    { rows: [{ name: "Host", value: "PRIVATE" }] },
    { rows: [{ name: "X-Auth", value: "PRIVATE\r\nInjected: true" }] },
    { rows: [{ name: "X-Auth", value: "" }] },
  ])("rejects invalid rows without echoing secret values", ({ rows }) => {
    expect(() => customHeaders(rows)).toThrow(/自定义请求头/);
    let failure: unknown;
    try {
      customHeaders(rows);
    } catch (error) {
      failure = error;
    }
    expect(String(failure)).not.toContain("PRIVATE");
  });
  it("requires credentials for the selected auth mode", () => {
    expect(() =>
      connectionFields({ name: "Gateway", provider: "custom", auth_type: "headers" }),
    ).toThrow("请求头认证");
    expect(() =>
      connectionFields({ name: "Gateway", provider: "custom", auth_type: "api_key" }),
    ).toThrow("API Key");
    expect(() =>
      connectionFields({
        name: "Gateway",
        provider: "custom",
        auth_type: "none",
        api_key: "PRIVATE",
      }),
    ).toThrow("无需认证");
  });
  it("represents the existing local ChatGPT OAuth session without accepting a token or API key", () => {
    const fields = connectionFields({
      name: " My plan ",
      provider: "chatgpt",
      auth_type: "oauth_local",
      api_protocol: "openai_responses",
    });
    expect(fields).toMatchObject({
      name: "My plan",
      endpoint: "https://api.openai.com/v1",
      auth_type: "oauth_local",
    });
    expect(fields).not.toHaveProperty("api_key");
    expect(fields).not.toHaveProperty("extra_headers");
  });
  it.each([
    { api_key: "PRIVATE" },
    { header_rows: [{ name: "Authorization", value: "PRIVATE" }] },
    { endpoint: "https://relay.example/v1" },
    { scope: "workspace" as const },
    { auth_type: "none" as const },
    { api_protocol: "openai_chat_completions" as const },
  ])("blocks replacing local OAuth with another credential or account path", (overrides) => {
    expect(() =>
      connectionFields({
        name: "My plan",
        provider: "chatgpt",
        auth_type: "oauth_local",
        ...overrides,
      }),
    ).toThrow("本机OAuth");
  });
});
