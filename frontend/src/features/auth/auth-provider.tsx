import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  getAuthSession,
  changeAccountPassword,
  loginAccount,
  logoutAccount,
  logoutAllSessions,
  registerAccount,
  setupOwnerAccount,
} from "../../shared/api/generated/client";
import type {
  AuthSessionStatus,
  AuthLoginRequest,
  AuthRegisterRequest,
  AuthSetupRequest,
  AuthPasswordChange,
} from "../../shared/api/generated/models";
import { GetAuthSessionResponse } from "../../shared/api/generated/validators";
import { ApiError } from "../../shared/api/transport";
import {
  clearAuthSession,
  installAuthSession,
  subscribeSessionExpired,
} from "../../shared/api/session-state";
import { AuthContext } from "./auth-context";
import type { AuthContextValue } from "./auth-context";

function isSignedIn(session: AuthSessionStatus | undefined) {
  return session?.authenticated === true && !!session.user && !!session.csrf_token;
}

function sameIdentity(first: AuthSessionStatus | undefined, second: AuthSessionStatus) {
  return (
    first?.authenticated === second.authenticated &&
    first?.user?.id === second.user?.id &&
    first?.user?.workspace.id === second.user?.workspace.id &&
    first?.csrf_token === second.csrf_token
  );
}

function safeError(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function operationInProgress() {
  return new ApiError({ status: 409, code: "auth_busy", message: "正在处理账号请求，请稍后重试" });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const cache = useQueryClient();
  const [session, setSession] = useState<AuthSessionStatus>();
  const [phase, setPhase] = useState<AuthContextValue["phase"]>("checking");
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [manualLogout, setManualLogout] = useState(false);
  const latest = useRef<AuthSessionStatus | undefined>(undefined);
  const activeRead = useRef<AbortController | undefined>(undefined);
  const sequence = useRef(0);
  const activeOperation = useRef(false);
  const channel = useRef<BroadcastChannel | undefined>(undefined);

  const abortRead = useCallback(() => {
    sequence.current += 1;
    activeRead.current?.abort();
    activeRead.current = undefined;
  }, []);

  const flushPrivateData = useCallback(async () => {
    await cache.cancelQueries(undefined, { silent: true });
    cache.clear();
  }, [cache]);

  const acceptStatus = useCallback(
    async (next: AuthSessionStatus, stillCurrent: () => boolean = () => true) => {
      if (!stillCurrent()) return;
      if (next.authenticated && (!next.user || !next.csrf_token))
        throw new ApiError({
          status: 0,
          code: "invalid_auth_session",
          message: "登录状态不完整，请重新登录",
        });
      if (sameIdentity(latest.current, next)) {
        latest.current = next;
        setSession(next);
        setPhase("ready");
        return;
      }
      // Unmount private pages before accepting a new cookie identity or touching caches.
      setPhase("transition");
      latest.current = undefined;
      setSession(undefined);
      clearAuthSession();
      await flushPrivateData();
      if (!stillCurrent()) return;
      if (isSignedIn(next) && next.user && next.csrf_token) {
        installAuthSession({
          userId: next.user.id,
          workspaceId: next.user.workspace.id,
          csrfToken: next.csrf_token,
        });
        setManualLogout(false);
      }
      latest.current = next;
      setSession(next);
      setPhase("ready");
    },
    [flushPrivateData],
  );

  const refresh = useCallback(async () => {
    if (activeOperation.current) return;
    abortRead();
    const controller = new AbortController();
    activeRead.current = controller;
    const ticket = sequence.current;
    try {
      const next = GetAuthSessionResponse.parse(
        await getAuthSession({ signal: controller.signal }),
      );
      if (ticket !== sequence.current || controller.signal.aborted) return;
      await acceptStatus(next, () => ticket === sequence.current && !controller.signal.aborted);
      if (ticket === sequence.current) setError(undefined);
    } catch (failure) {
      if (
        ticket !== sequence.current ||
        controller.signal.aborted ||
        (failure instanceof Error && failure.name === "AbortError")
      )
        return;
      setError(safeError(failure, "无法确认登录状态，请检查本机服务后重试"));
      if (!latest.current) setPhase("error");
    } finally {
      if (activeRead.current === controller) activeRead.current = undefined;
    }
  }, [abortRead, acceptStatus]);

  const expire = useCallback(
    async (message: string) => {
      abortRead();
      setPhase("checking");
      latest.current = undefined;
      setSession(undefined);
      clearAuthSession();
      setNotice(message);
      await flushPrivateData();
      await refresh();
    },
    [abortRead, flushPrivateData, refresh],
  );

  useEffect(() => {
    let alive = true;
    // Attach expiration listeners first, and discard the initial read if this
    // provider was removed by StrictMode or a root remount before it starts.
    queueMicrotask(() => {
      if (alive) void refresh();
    });
    const unsubscribe = subscribeSessionExpired(() => {
      void expire("登录状态已失效，请重新登录。");
    });
    const check = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, 60_000);
    if (typeof BroadcastChannel !== "undefined") {
      const broadcast = new BroadcastChannel("tradingagents-session");
      channel.current = broadcast;
      broadcast.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === "identity-changed") void expire("登录状态已在其他窗口更新。");
      };
    }
    return () => {
      alive = false;
      abortRead();
      unsubscribe();
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
      window.clearInterval(timer);
      channel.current?.close();
      channel.current = undefined;
      clearAuthSession();
    };
  }, [abortRead, expire, refresh]);

  const authenticate = useCallback(
    async (
      operation: () => Promise<AuthSessionStatus>,
      successNotice?: string,
      allowSignedOut = false,
    ) => {
      if (activeOperation.current) throw operationInProgress();
      activeOperation.current = true;
      abortRead();
      const ticket = sequence.current;
      setError(undefined);
      try {
        const next = GetAuthSessionResponse.parse(await operation());
        if (!isSignedIn(next) && !allowSignedOut)
          throw new ApiError({
            status: 0,
            code: "invalid_auth_session",
            message: "服务未建立有效登录，请重试",
          });
        if (ticket !== sequence.current)
          throw new DOMException("Authentication changed in another window", "AbortError");
        await acceptStatus(next, () => ticket === sequence.current);
        setNotice(successNotice);
        if (!next.authenticated) setManualLogout(true);
        channel.current?.postMessage("identity-changed");
      } finally {
        activeOperation.current = false;
        if (!latest.current) void refresh();
      }
    },
    [abortRead, acceptStatus, refresh],
  );

  const endSession = useCallback(
    async (allSessions: boolean) => {
      if (activeOperation.current) throw operationInProgress();
      activeOperation.current = true;
      abortRead();
      const ticket = sequence.current;
      setPhase("transition");
      setError(undefined);
      try {
        const next = GetAuthSessionResponse.parse(
          await (allSessions ? logoutAllSessions() : logoutAccount()),
        );
        if (next.authenticated)
          throw new ApiError({
            status: 0,
            code: "logout_incomplete",
            message: "服务器尚未结束会话，请重试",
          });
        await acceptStatus(next, () => ticket === sequence.current);
        setManualLogout(true);
        setNotice(allSessions ? "已退出此账号的所有会话。" : "已退出登录。");
        channel.current?.postMessage("identity-changed");
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 401) {
          clearAuthSession();
          setManualLogout(true);
          latest.current = undefined;
          setSession(undefined);
          await flushPrivateData();
          setPhase("checking");
        } else {
          setError(safeError(failure, "未能结束服务器会话，请重新尝试退出登录"));
          setPhase("ready");
          throw failure;
        }
      } finally {
        activeOperation.current = false;
        if (!latest.current) void refresh();
      }
    },
    [abortRead, acceptStatus, flushPrivateData, refresh],
  );

  const value: AuthContextValue = {
    session,
    phase,
    error,
    notice,
    manualLogout,
    refresh,
    logout: () => endSession(false),
    logoutAll: () => endSession(true),
    changePassword: (body: AuthPasswordChange) =>
      authenticate(
        () => changeAccountPassword(body),
        "密码已更新，其他设备上的旧会话已退出。",
        true,
      ),
    login: (body: AuthLoginRequest) => authenticate(() => loginAccount(body)),
    register: (body: AuthRegisterRequest) => authenticate(() => registerAccount(body)),
    setup: (body: AuthSetupRequest) => authenticate(() => setupOwnerAccount(body)),
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
