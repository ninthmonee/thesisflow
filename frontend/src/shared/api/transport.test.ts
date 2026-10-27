import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, shouldRetryQuery } from "./transport";
import {
  authGeneration,
  authWorkspaceId,
  clearAuthSession,
  installAuthSession,
  subscribeSessionExpired,
} from "./session-state";

afterEach(() => {
  vi.unstubAllGlobals();
  clearAuthSession();
});

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

  it("binds business requests to the confirmed workspace and writes to the memory CSRF token", async () => {
    installAuthSession({ userId: "one", workspaceId: "space-one", csrfToken: "csrf-one" });
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/api/v1/watchlists", {
      method: "POST",
      body: "{}",
      headers: { "X-Workspace-ID": "forged", "X-CSRF-Token": "forged" },
    });
    const written = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(written.get("X-Workspace-ID")).toBe("space-one");
    expect(written.get("X-CSRF-Token")).toBe("csrf-one");
    await apiFetch("/api/v1/watchlists");
    const read = new Headers(fetchMock.mock.calls[1]?.[1]?.headers);
    expect(read.get("X-Workspace-ID")).toBe("space-one");
    expect(read.has("X-CSRF-Token")).toBe(false);
    await apiFetch("/api/v1/auth/session");
    expect(new Headers(fetchMock.mock.calls[2]?.[1]?.headers).has("X-Workspace-ID")).toBe(false);
  });

  it("does not globally expire the session for a login failure", async () => {
    installAuthSession({ userId: "one", workspaceId: "space-one", csrfToken: "csrf-one" });
    const expired = vi.fn<() => void>();
    const unsubscribe = subscribeSessionExpired(expired);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    try {
      await expect(
        apiFetch("/api/v1/auth/login", { method: "POST", body: "{}" }),
      ).rejects.toMatchObject({ status: 401 });
      expect(expired).not.toHaveBeenCalled();
      expect(authWorkspaceId()).toBe("space-one");
    } finally {
      unsubscribe();
    }
  });

  it("invalidates a current authenticated session on a business 401", async () => {
    installAuthSession({ userId: "one", workspaceId: "space-one", csrfToken: "csrf-one" });
    const expired = vi.fn<() => void>();
    const unsubscribe = subscribeSessionExpired(expired);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    try {
      await expect(apiFetch("/api/v1/watchlists")).rejects.toMatchObject({ status: 401 });
      expect(expired).toHaveBeenCalledTimes(1);
      expect(authWorkspaceId()).toBeUndefined();
    } finally {
      unsubscribe();
    }
  });

  it.each([200, 401])(
    "discards a late HTTP %s response belonging to an earlier session",
    async (status) => {
      installAuthSession({ userId: "one", workspaceId: "space-one", csrfToken: "csrf-one" });
      let finish: ((response: Response) => void) | undefined;
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>().mockImplementation(
          () =>
            new Promise((resolve) => {
              finish = resolve;
            }),
        ),
      );
      const oldGeneration = authGeneration();
      const pending = apiFetch("/api/v1/watchlists");
      installAuthSession({ userId: "two", workspaceId: "space-two", csrfToken: "csrf-two" });
      finish?.(new Response('{"private":"earlier-user"}', { status }));
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(authGeneration()).toBeGreaterThan(oldGeneration);
      expect(authWorkspaceId()).toBe("space-two");
    },
  );
});
