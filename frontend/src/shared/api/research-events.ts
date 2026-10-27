import { GetResearchEventsResponse } from "./generated/validators";
import { ApiError } from "./transport";
import {
  assertCurrentSession,
  authGeneration,
  authWorkspaceId,
  notifySessionExpired,
} from "./session-state";

export const researchEventSchema = GetResearchEventsResponse.shape.events.element;
export type ResearchEvent = ReturnType<typeof researchEventSchema.parse>;

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** Incremental framing; JSON and event contracts are validated separately. */
export function createSseDecoder(emit: (frame: SseFrame) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer = (buffer + chunk).replace(/\r\n/g, "\n");
    if (buffer.length > 2_000_000) throw new Error("SSE frame exceeds limit");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const lines = buffer.slice(0, boundary).split("\n");
      buffer = buffer.slice(boundary + 2);
      const data: string[] = [];
      let id: string | undefined;
      let event: string | undefined;
      for (const line of lines) {
        if (!line || line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator < 0 ? line : line.slice(0, separator);
        const value = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
        if (field === "data") data.push(value);
        else if (field === "id" && !value.includes("\0")) id = value;
        else if (field === "event") event = value;
      }
      if (data.length) emit({ id, event, data: data.join("\n") });
      boundary = buffer.indexOf("\n\n");
    }
  };
}

export async function readResearchEvents(options: {
  runId: string;
  signal: AbortSignal;
  cursor?: string;
  onOpen?: () => void;
  onEvent: (event: ResearchEvent) => void;
}): Promise<string | undefined> {
  const headers = new Headers({ Accept: "text/event-stream" });
  const requestGeneration = authGeneration();
  const workspaceId = authWorkspaceId();
  if (workspaceId) headers.set("X-Workspace-ID", workspaceId);
  if (options.cursor) headers.set("Last-Event-ID", options.cursor);
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(options.runId)}/events`, {
    signal: options.signal,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  assertCurrentSession(requestGeneration);
  if (response.status === 401) notifySessionExpired(requestGeneration);
  if (
    !response.ok ||
    !response.body ||
    !response.headers.get("Content-Type")?.includes("text/event-stream")
  ) {
    throw new ApiError({
      status: response.status,
      code: response.status === 422 ? "invalid_event_cursor" : "event_stream_unavailable",
      message: "实时事件连接暂不可用，使用状态轮询",
      retryable: response.status >= 500,
    });
  }
  options.onOpen?.();
  let cursor = options.cursor;
  let sequence = cursor ? Number(cursor.slice(cursor.lastIndexOf(":") + 1)) : 0;
  const decode = createSseDecoder((frame) => {
    const event = researchEventSchema.parse(JSON.parse(frame.data));
    if (
      event.run_id !== options.runId ||
      event.event_id !== `${options.runId}:${event.seq}` ||
      (frame.id && frame.id !== event.event_id) ||
      (frame.event && frame.event !== event.type)
    )
      throw new Error("Event ownership or framing mismatch");
    if (event.seq <= sequence) return;
    if (sequence && event.seq !== sequence + 1)
      throw new ApiError({
        status: 0,
        code: "event_gap",
        message: "事件序号不连续，需要同步最新状态",
      });
    if (options.signal.aborted) return;
    sequence = event.seq;
    cursor = event.event_id;
    options.onEvent(event);
  });
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  try {
    while (!options.signal.aborted) {
      const part = await reader.read();
      assertCurrentSession(requestGeneration);
      if (part.done) {
        decode(decoder.decode());
        break;
      }
      decode(decoder.decode(part.value, { stream: true }));
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  return cursor;
}

export function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
  });
}
