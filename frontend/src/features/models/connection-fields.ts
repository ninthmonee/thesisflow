import type { ConnectionCreate, ConnectionView } from "../../shared/api/generated/models";
import { ApiError } from "../../shared/api/transport";

export interface HeaderRow {
  name?: string;
  value?: string;
}
export interface ConnectionEditorFields extends Omit<ConnectionCreate, "extra_headers"> {
  header_rows?: HeaderRow[];
  clear_extra_headers?: boolean;
}
export const protocolLabels: Record<string, string> = {
  auto: "按预设自动选择",
  openai_chat_completions: "OpenAI Chat Completions 兼容",
  openai_responses: "OpenAI Responses 兼容",
  anthropic_messages: "Anthropic Messages 兼容",
  google_genai: "Google Gemini 原生",
};
export const protocolDescriptions: Record<string, string> = {
  auto: "沿用预设协议。原生 OpenAI 使用 Responses，自定义 OpenAI 地址按 Chat Completions；其他预设保持原生协议。",
  openai_chat_completions:
    "使用 /chat/completions；通常填写包含 /v1 的基础地址。模型 ID 可直接手填，无需在内置列表中。",
  openai_responses:
    "使用 /responses，服务必须明确实现 Responses，不能仅凭支持 Chat Completions 推断。通常填写包含 /v1 的基础地址。",
  anthropic_messages:
    "使用 /v1/messages；基础地址通常不含尾部 /v1。自定义地址必须提供 Anthropic 兼容消息及工具格式。",
  google_genai:
    "使用 Google GenAI 原生协议；可配置其兼容基础地址。专有协议仍需新增适配器，保存连接后先检测能力。",
};

function invalid(message: string): never {
  throw new ApiError({ status: 0, code: "invalid_connection", message });
}

export function customHeaders(rows: HeaderRow[] | undefined): Record<string, string> | undefined {
  if (!rows?.length) return undefined;
  if (rows.length > 16) invalid("最多支持16个自定义请求头");
  const blocked = new Set([
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "cookie",
    "set-cookie",
    "origin",
    "referer",
  ]);
  const result = new Map<string, string>(),
    names = new Set<string>();
  for (const row of rows) {
    const key = row.name?.trim() ?? "",
      value = row.value ?? "";
    if (!key && !value) continue;
    if (
      !/^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}$/.test(key) ||
      blocked.has(key.toLowerCase()) ||
      names.has(key.toLowerCase())
    ) {
      invalid("自定义请求头名称无效或重复");
    }
    if (
      !value ||
      value.length > 4096 ||
      ["\r", "\n", "\u0000"].some((control) => value.includes(control))
    )
      invalid("自定义请求头值无效");
    names.add(key.toLowerCase());
    result.set(key, value);
  }
  return result.size ? Object.fromEntries(result) : undefined;
}

/** Allowlist API fields; form-only secret rows never enter a request or Query cache. */
export function connectionFields(
  values: ConnectionEditorFields,
  existing?: ConnectionView | null,
): Omit<ConnectionCreate, "provider" | "scope"> {
  const headers = customHeaders(values.header_rows),
    auth = values.auth_type ?? "api_key";
  if (headers && values.clear_extra_headers) invalid("不能同时替换与清除自定义请求头");
  if (values.provider === "chatgpt") {
    if (
      auth !== "oauth_local" ||
      values.api_key ||
      headers ||
      (values.scope && values.scope !== "private") ||
      (values.endpoint &&
        values.endpoint.trim().replace(/\/$/, "") !== "https://api.openai.com/v1") ||
      (values.api_protocol && !["auto", "openai_responses"].includes(values.api_protocol))
    )
      invalid("ChatGPT连接仅使用本机OAuth授权，不能配置API Key、请求头、共享范围或其他接口地址");
  } else if (auth === "oauth_local") invalid("本机OAuth授权仅用于ChatGPT订阅连接");
  if (auth === "api_key" && !values.api_key && !existing?.credential_configured)
    invalid("请输入 API Key");
  if (
    auth === "headers" &&
    !headers &&
    (!existing?.headers_configured || values.clear_extra_headers)
  )
    invalid("请求头认证需要至少一个凭据请求头");
  if (auth === "none" && (values.api_key || headers))
    invalid("无需认证连接不应填写凭据；请清空或选择相应认证方式");
  return {
    name: values.name.trim(),
    vendor_name: values.vendor_name?.trim() || null,
    endpoint:
      values.provider === "chatgpt" ? "https://api.openai.com/v1" : values.endpoint?.trim() || null,
    api_protocol: values.api_protocol ?? "auto",
    auth_type: auth,
    enabled: values.enabled ?? true,
    ...(auth === "api_key" && values.api_key ? { api_key: values.api_key } : {}),
    ...(headers ? { extra_headers: headers } : {}),
  };
}
