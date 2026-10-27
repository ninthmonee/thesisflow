import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { abortableDelay, readResearchEvents } from "../../shared/api/research-events";
import type { ResearchEvent } from "../../shared/api/research-events";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError } from "../../shared/api/transport";

export const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function useResearchEvents(scope: QueryScope, runId: string | undefined, enabled: boolean) {
  const cache = useQueryClient();
  const streamKey = JSON.stringify([scope.userId, scope.workspaceId, runId]);
  const [connection, setConnection] = useState<{ key: string; value: "live" | "polling" }>({
    key: "",
    value: "polling",
  });
  const [events, setEvents] = useState<{ key: string; values: ResearchEvent[] }>({
    key: "",
    values: [],
  });
  useEffect(() => {
    if (!runId || !enabled) {
      return;
    }
    const controller = new AbortController();
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const key = queryKeys.resource(
      { userId: scope.userId, workspaceId: scope.workspaceId },
      "research-run",
      { runId },
    );
    const refresh = () => {
      void cache.invalidateQueries({ queryKey: key });
    };
    const run = async () => {
      let cursor: string | undefined;
      let retries = 0;
      while (!controller.signal.aborted) {
        try {
          cursor = await readResearchEvents({
            runId,
            signal: controller.signal,
            cursor,
            onOpen: () => {
              if (!controller.signal.aborted) setConnection({ key: streamKey, value: "live" });
            },
            onEvent: (event) => {
              cursor = event.event_id;
              retries = 0;
              setEvents((old) => {
                const previous = old.key === streamKey ? old.values : [];
                if (previous.some((item) => item.event_id === event.event_id)) return old;
                return { key: streamKey, values: [...previous, event].slice(-80) };
              });
              if (event.type === "state" && terminalStatuses.has(event.payload.status)) refresh();
              if (event.type === "resync_required") {
                refresh();
                return;
              }
              if (!refreshTimer)
                refreshTimer = setTimeout(() => {
                  refreshTimer = undefined;
                  refresh();
                }, 300);
            },
          });
        } catch (error) {
          if (controller.signal.aborted) break;
          if (
            error instanceof ApiError &&
            (error.code === "invalid_event_cursor" || error.code === "event_gap")
          )
            cursor = undefined;
          setConnection({ key: streamKey, value: "polling" });
          refresh();
        }
        if (!controller.signal.aborted)
          await abortableDelay(
            Math.min(1000 * 2 ** Math.min(retries++, 4), 15_000),
            controller.signal,
          );
      }
      if (!controller.signal.aborted) {
        refresh();
      }
    };
    void run();
    return () => {
      controller.abort();
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, [cache, runId, enabled, scope.userId, scope.workspaceId, streamKey]);
  return {
    connection: !enabled ? "ended" : connection.key === streamKey ? connection.value : "connecting",
    events: events.key === streamKey ? events.values : [],
  };
}
