import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, shouldRetryQuery } from "./transport";

afterEach(() => vi.unstubAllGlobals());

describe("API transport", () => {
  it.each([401, 422, 429])(
    "rejects HTTP %s rather than resolving an error body",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(
            JSON.stringify({
              error_code: "test_error",
              message: "Safe failure",
              trace_id: "trace-1",
            }),
            { status },
          ),
        ),
      );
      await expect(apiFetch("/api/v1/test")).rejects.toMatchObject({
        name: "ApiError",
        status,
        code: "test_error",
        message: "Safe failure",
        traceId: "trace-1",
      });
    },
  );

  it("preserves abort and does not retry a cancelled request", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError")));
    await expect(apiFetch("/api/v1/test", { signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(shouldRetryQuery(0, new DOMException("Aborted", "AbortError"))).toBe(false);
  });

  it("classifies network failures and only retries a bounded number", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(apiFetch("/api/v1/test")).rejects.toMatchObject({
      code: "network_error",
      status: 0,
    });
    const error = new ApiError({ status: 429, code: "limited", message: "Retry", retryable: true });
    expect(shouldRetryQuery(0, error)).toBe(true);
    expect(shouldRetryQuery(2, error)).toBe(false);
    expect(shouldRetryQuery(0, new ApiError({ status: 401, code: "auth", message: "Auth" }))).toBe(
      false,
    );
  });

  it("forwards signal and same-origin credentials for successful requests", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"ok":true}'));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await expect(apiFetch("/api/v1/test", { signal: controller.signal })).resolves.toEqual({
      ok: true,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/test",
      expect.objectContaining({
        signal: controller.signal,
        credentials: "same-origin",
      }),
    );
  });

  it("does not request arbitrary external URLs", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("https://vendor.example/api/test")).rejects.toMatchObject({
      code: "invalid_api_url",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
