export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId: string | undefined;
  readonly retryable: boolean;

  constructor(options: {
    status: number;
    code: string;
    message: string;
    traceId?: string;
    retryable?: boolean;
  }) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.traceId = options.traceId;
    this.retryable = options.retryable ?? false;
  }
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function errorDetails(payload: unknown, status: number, traceId?: string): ApiError {
  const record = asRecord(payload);
  const detail = asRecord(record?.detail) ?? record;
  return new ApiError({
    status,
    code: typeof detail?.error_code === "string" ? detail.error_code : `http_${status}`,
    message:
      typeof detail?.message === "string"
        ? detail.message
        : typeof record?.detail === "string"
          ? record.detail
          : `请求失败（HTTP ${status}）`,
    traceId: typeof detail?.trace_id === "string" ? detail.trace_id : traceId,
    retryable:
      typeof detail?.retryable === "boolean" ? detail.retryable : status === 429 || status >= 500,
  });
}

/** Shared Orval mutator. HTTP errors must reject so Query enters its error state. */
export async function apiFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  if (!url.startsWith("/api/")) {
    throw new ApiError({ status: 0, code: "invalid_api_url", message: "请求地址不属于本地 API" });
  }
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  headers.set("X-Request-ID", crypto.randomUUID());
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (error) {
    if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
      throw error;
    }
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: "无法连接本机服务，请检查服务是否已启动",
      retryable: true,
    });
  }
  const raw = await response.text();
  let payload: unknown;
  try {
    payload = raw ? JSON.parse(raw) : undefined;
  } catch {
    if (!response.ok)
      throw errorDetails(
        undefined,
        response.status,
        response.headers.get("X-Request-ID") ?? undefined,
      );
    throw new ApiError({
      status: response.status,
      code: "invalid_json",
      message: "服务返回了无法读取的数据",
    });
  }
  if (!response.ok) {
    throw errorDetails(payload, response.status, response.headers.get("X-Request-ID") ?? undefined);
  }
  return payload as T;
}

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof Error && error.name === "AbortError") return false;
  return error instanceof ApiError && error.retryable && failureCount < 2;
}
