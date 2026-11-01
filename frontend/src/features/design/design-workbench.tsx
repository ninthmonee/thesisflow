import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/auth-context";
import { createInvestmentService } from "./investment-service";
import { createMarketService } from "./market-service";
import { createResearchService } from "./research-service";

const protocol = "tradingagents-design-v1";

/** Keep the reference's document styles isolated while sharing the real session/SDK. */
export function DesignWorkbench() {
  const frame = useRef<HTMLIFrameElement>(null);
  const location = useLocation();
  const initialPath = useRef(location.pathname + location.search);
  const navigate = useNavigate();
  const auth = useAuth();
  const [failure, setFailure] = useState(false);
  const user = auth.session?.user;
  const latest = useRef({ auth, navigate, user });
  useEffect(() => {
    latest.current = { auth, navigate, user };
  }, [auth, navigate, user]);
  const [handlers] = useState(() => [
    createResearchService(),
    createInvestmentService(),
    createMarketService(),
  ]);
  const identity = user ? `${user.id}:${user.workspace.id}` : "";

  useEffect(() => {
    if (!identity) return;
    let active = true;
    const receive = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow)
        return;
      const data = event.data;
      if (!data || typeof data !== "object" || !("protocol" in data) || data.protocol !== protocol)
        return;
      if (
        !("id" in data) ||
        typeof data.id !== "string" ||
        !("method" in data) ||
        typeof data.method !== "string"
      )
        return;
      const id = data.id;
      const method = data.method;
      const payload: unknown = "payload" in data ? data.payload : undefined;
      const execute = async () => {
        if (method === "host.session")
          return {
            user: {
              id: latest.current.user?.id,
              workspaceId: latest.current.user?.workspace.id,
              displayName: latest.current.user?.display_name,
            },
            path: initialPath.current,
          };
        if (method === "host.open") {
          const path =
            payload &&
            typeof payload === "object" &&
            "path" in payload &&
            typeof payload.path === "string"
              ? payload.path
              : "/";
          const target = new URL(path, window.location.origin);
          if (
            target.origin !== window.location.origin ||
            !/^\/(settings(?:\/[^?#]*)?|account|stocks(?:\/[^?#]*)?|market|sectors|themes|insights\/(?:flows|options|news)|research\/new|runs\/[^/?#]+|reports|investment)$/.test(
              target.pathname,
            )
          )
            throw new Error("不支持的工作区地址");
          target.searchParams.set("embedded", "1");
          return {
            url: `${import.meta.env.BASE_URL}advanced${target.pathname}${target.search}`,
            path: target.pathname,
          };
        }
        if (method === "host.navigate") {
          const path =
            payload &&
            typeof payload === "object" &&
            "path" in payload &&
            typeof payload.path === "string"
              ? payload.path
              : "/";
          if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
            throw new Error("无效地址");
          void latest.current.navigate(path, {
            replace: !!(
              payload &&
              typeof payload === "object" &&
              "replace" in payload &&
              payload.replace
            ),
          });
          return null;
        }
        if (method === "host.logout") {
          await latest.current.auth.logout();
          return null;
        }
        for (const handler of handlers) {
          const value = await handler(method, payload);
          if (value !== undefined) return value;
        }
        throw new Error("此操作暂不受支持");
      };
      void execute().then(
        (result) => {
          if (active)
            frame.current?.contentWindow?.postMessage(
              { protocol, id, result },
              window.location.origin,
            );
        },
        (error: unknown) => {
          if (active)
            frame.current?.contentWindow?.postMessage(
              { protocol, id, error: error instanceof Error ? error.message : "操作失败，请重试" },
              window.location.origin,
            );
        },
      );
    };
    window.addEventListener("message", receive);
    return () => {
      active = false;
      window.removeEventListener("message", receive);
    };
  }, [handlers, identity]);

  useEffect(() => {
    const update = () =>
      frame.current?.contentWindow?.postMessage(
        {
          protocol,
          event: "location",
          path:
            window.location.pathname.replace(import.meta.env.BASE_URL.replace(/\/$/, ""), "") +
            window.location.search,
        },
        window.location.origin,
      );
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);

  return (
    <>
      {failure && (
        <p role="alert">
          工作台加载失败。<button onClick={() => window.location.reload()}>重新加载</button>
        </p>
      )}
      <iframe
        ref={frame}
        title="研衡 · ThesisFlow 个人智能投研与决策工作台"
        src={`${import.meta.env.BASE_URL}design/index.html`}
        onError={() => setFailure(true)}
        style={{
          display: "block",
          border: 0,
          width: "100%",
          height: "100dvh",
          background: "#0b1017",
        }}
      />
    </>
  );
}
