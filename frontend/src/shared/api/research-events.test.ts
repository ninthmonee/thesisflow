import { afterEach, describe, expect, it, vi } from "vitest";
import { createSseDecoder, readResearchEvents } from "./research-events";

afterEach(() => vi.unstubAllGlobals());
const runId = "e7818f34-d248-4e34-aafe-47882a8fffee";
const envelope = (seq: number, id = runId) => ({
  event_id: `${id}:${seq}`,
  seq,
  run_id: id,
  attempt_id: null,
  timestamp: "2026-10-24T10:00:00Z",
  schema_version: "1",
  type: "progress",
  payload: { stage: "research", label: "多空研究" },
});
function stream(frames: unknown[]) {
  const text = frames.map((item) => `data: ${JSON.stringify(item)}\n\n`).join("");
  return new Response(new TextEncoder().encode(text), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("research event stream", () => {
  it("frames split CRLF and multiline data while ignoring heartbeats", () => {
    const emit = vi.fn<(frame: unknown) => void>();
    const decode = createSseDecoder(emit);
    decode(": ping\r");
    decode("\n\r\nid: cursor\r\nevent: progress\r\ndata: one\r\ndata: two\r\n\r");
    decode("\n");
    expect(emit).toHaveBeenCalledExactlyOnceWith({
      id: "cursor",
      event: "progress",
      data: "one\ntwo",
    });
  });
  it("validates events and suppresses replayed sequence numbers", async () => {
    const emit = vi.fn<(event: unknown) => void>();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(stream([envelope(1), envelope(2)]));
    vi.stubGlobal("fetch", fetchMock);
    const cursor = await readResearchEvents({
      runId,
      cursor: `${runId}:1`,
      signal: new AbortController().signal,
      onEvent: emit,
    });
    expect(emit).toHaveBeenCalledTimes(1);
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ seq: 2 }));
    expect(cursor).toBe(`${runId}:2`);
    const options = fetchMock.mock.calls[0]?.[1];
    expect(new Headers(options?.headers).get("Last-Event-ID")).toBe(`${runId}:1`);
    expect(options?.credentials).toBe("same-origin");
  });
  it("rejects cross-run data and invalid schema", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(stream([envelope(1, "9d68e247-b0f5-4d44-b95a-7a24862c7689")])),
    );
    await expect(
      readResearchEvents({ runId, signal: new AbortController().signal, onEvent: () => {} }),
    ).rejects.toThrow("ownership");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(stream([{ type: "progress" }])));
    await expect(
      readResearchEvents({ runId, signal: new AbortController().signal, onEvent: () => {} }),
    ).rejects.toHaveProperty("name", "ZodError");
  });
  it("signals gaps rather than silently skipping evidence", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(stream([envelope(3)])));
    await expect(
      readResearchEvents({
        runId,
        cursor: `${runId}:1`,
        signal: new AbortController().signal,
        onEvent: () => {},
      }),
    ).rejects.toMatchObject({ code: "event_gap" });
  });
  it("reports an expired cursor without consuming JSON as an event stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 422 })),
    );
    await expect(
      readResearchEvents({ runId, signal: new AbortController().signal, onEvent: () => {} }),
    ).rejects.toMatchObject({ code: "invalid_event_cursor", status: 422 });
  });
});
