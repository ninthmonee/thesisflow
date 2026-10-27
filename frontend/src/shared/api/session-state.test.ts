import { afterEach, describe, expect, it, vi } from "vitest";
import {
  assertCurrentSession,
  authGeneration,
  authWorkspaceId,
  clearAuthSession,
  csrfToken,
  installAuthSession,
  notifySessionExpired,
  subscribeSessionExpired,
} from "./session-state";

afterEach(() => clearAuthSession());

describe("memory-only session ownership", () => {
  it("keeps the same request generation for a status refresh of the same session", () => {
    const identity = { userId: "one", workspaceId: "workspace-one", csrfToken: "csrf-one" };
    installAuthSession(identity);
    const first = authGeneration();
    installAuthSession({ ...identity });
    expect(authGeneration()).toBe(first);
    expect(authWorkspaceId()).toBe("workspace-one");
    expect(csrfToken()).toBe("csrf-one");
  });

  it("revokes old responses on an identity, workspace or cookie-session change", () => {
    installAuthSession({ userId: "one", workspaceId: "workspace-one", csrfToken: "csrf-one" });
    const first = authGeneration();
    installAuthSession({ userId: "two", workspaceId: "workspace-two", csrfToken: "csrf-two" });
    expect(() => assertCurrentSession(first)).toThrow("earlier session");
    const second = authGeneration();
    installAuthSession({ userId: "two", workspaceId: "workspace-next", csrfToken: "csrf-two" });
    expect(() => assertCurrentSession(second)).toThrow("earlier session");
    const third = authGeneration();
    installAuthSession({ userId: "two", workspaceId: "workspace-next", csrfToken: "rotated-csrf" });
    expect(() => assertCurrentSession(third)).toThrow("earlier session");
  });

  it("does not let an old tab response revoke a newer authenticated identity", () => {
    installAuthSession({ userId: "one", workspaceId: "workspace-one", csrfToken: "csrf-one" });
    const old = authGeneration();
    installAuthSession({ userId: "two", workspaceId: "workspace-two", csrfToken: "csrf-two" });
    const listener = vi.fn<() => void>();
    const unsubscribe = subscribeSessionExpired(listener);
    try {
      notifySessionExpired(old);
      expect(listener).not.toHaveBeenCalled();
      expect(authWorkspaceId()).toBe("workspace-two");
      expect(csrfToken()).toBe("csrf-two");
    } finally {
      unsubscribe();
    }
  });

  it("notifies only once, erases request credentials and prevents a 401 loop", () => {
    installAuthSession({ userId: "one", workspaceId: "workspace-one", csrfToken: "csrf-one" });
    const active = authGeneration();
    const listener = vi.fn<() => void>();
    const unsubscribe = subscribeSessionExpired(listener);
    try {
      notifySessionExpired(active);
      notifySessionExpired(active);
      notifySessionExpired();
      clearAuthSession();
      notifySessionExpired();
      expect(listener).toHaveBeenCalledTimes(1);
      expect(csrfToken()).toBeUndefined();
      expect(authWorkspaceId()).toBeUndefined();
      expect(() => assertCurrentSession(active)).toThrow("earlier session");
    } finally {
      unsubscribe();
    }
  });
});
