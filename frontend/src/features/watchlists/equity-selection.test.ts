import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { WatchlistResponse } from "../../shared/api/generated/models";
import { readChartSettings, writeChartSettings } from "../market/chart-settings";
import { resolveEquitySelection } from "./equity-selection";

const nvda = {
  id: "instrument-nvda",
  symbol: "NVDA",
  name: "NVIDIA",
  asset_type: "stock",
  exchange: "NASDAQ",
  currency: "USD",
  timezone: "America/New_York",
};
const msft = { ...nvda, id: "instrument-msft", symbol: "MSFT", name: "Microsoft" };
const groups: WatchlistResponse[] = [
  {
    id: "first",
    name: "科技",
    position: 0,
    revision: 1,
    items: [{ id: "item-nvda", instrument: nvda, position: 0 }],
  },
  {
    id: "second",
    name: "另一组",
    position: 1,
    revision: 1,
    items: [{ id: "item-msft", instrument: msft, position: 0 }],
  },
];

describe("equity route selection", () => {
  it("waits for a real asynchronous watchlist query before canonicalizing an instrument route", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const queryKey = ["workspace", "one", "watchlists"];
    let fulfill: ((value: WatchlistResponse[]) => void) | undefined;
    const deferred = new Promise<WatchlistResponse[]>((resolve) => {
      fulfill = resolve;
    });
    const request = client.fetchQuery({ queryKey, queryFn: () => deferred });
    try {
      const initial = resolveEquitySelection({
        instrumentId: nvda.id,
        groups: client.getQueryData<WatchlistResponse[]>(queryKey),
        status: client.getQueryState(queryKey)?.status ?? "pending",
      });
      expect(initial).toEqual({ state: "loading" });
      const original = new URLSearchParams("interval=5m&period=5d");
      const unresolvedUrl = writeChartSettings(original, readChartSettings(original));
      if (initial.state === "ready") unresolvedUrl.set("symbol", initial.symbol);
      expect(unresolvedUrl.has("symbol")).toBe(false);
      expect(unresolvedUrl.toString()).not.toContain("SPY");
      if (!fulfill) throw new Error("Deferred watchlist query was not initialized");
      fulfill(groups);
      await request;
      const resolved = resolveEquitySelection({
        instrumentId: nvda.id,
        groups: client.getQueryData<WatchlistResponse[]>(queryKey),
        status: client.getQueryState(queryKey)?.status ?? "pending",
      });
      expect(resolved).toEqual({ state: "ready", symbol: "NVDA", instrument: nvda });
      if (resolved.state === "ready") unresolvedUrl.set("symbol", resolved.symbol);
      expect(unresolvedUrl.get("symbol")).toBe("NVDA");
    } finally {
      client.clear();
    }
  });

  it("does not turn a failed watchlist request into a SPY route", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const queryKey = ["workspace", "one", "failed-watchlists"];
    try {
      await expect(
        client.fetchQuery({
          queryKey,
          queryFn: () => Promise.reject(new Error("offline fixture")),
        }),
      ).rejects.toThrow("offline fixture");
      expect(
        resolveEquitySelection({
          instrumentId: nvda.id,
          groups: client.getQueryData<WatchlistResponse[]>(queryKey),
          status: client.getQueryState(queryKey)?.status ?? "pending",
        }),
      ).toEqual({ state: "error" });
      expect(resolveEquitySelection({ status: "error" })).toEqual({ state: "error" });
    } finally {
      client.clear();
    }
  });

  it("leaves unknown instrument IDs missing even when the list is empty or cached", () => {
    expect(resolveEquitySelection({ instrumentId: "unknown", groups, status: "success" })).toEqual({
      state: "missing",
    });
    expect(
      resolveEquitySelection({ instrumentId: "unknown", groups: [], status: "success" }),
    ).toEqual({ state: "missing" });
    expect(resolveEquitySelection({ instrumentId: "unknown", status: "success" })).toEqual({
      state: "missing",
    });
  });

  it("allows a normalized explicit symbol to override an unresolved or different ID", () => {
    expect(
      resolveEquitySelection({
        instrumentId: nvda.id,
        explicitSymbol: " msft ",
        status: "pending",
      }),
    ).toEqual({ state: "ready", symbol: "MSFT" });
    expect(
      resolveEquitySelection({
        instrumentId: nvda.id,
        explicitSymbol: " msft ",
        groups,
        status: "success",
      }),
    ).toEqual({ state: "ready", symbol: "MSFT", instrument: msft });
    expect(resolveEquitySelection({ explicitSymbol: " ^vix ", status: "pending" })).toEqual({
      state: "ready",
      symbol: "^VIX",
    });
  });

  it("ignores invalid explicit codes and keeps cached identity during refetch errors", () => {
    expect(
      resolveEquitySelection({
        instrumentId: nvda.id,
        explicitSymbol: "bad code",
        status: "pending",
      }),
    ).toEqual({ state: "loading" });
    expect(
      resolveEquitySelection({
        instrumentId: nvda.id,
        explicitSymbol: "bad code",
        groups,
        status: "error",
      }),
    ).toEqual({ state: "ready", symbol: "NVDA", instrument: nvda });
  });

  it("selects the active group's first instrument and uses SPY only after an empty list resolves", () => {
    expect(resolveEquitySelection({ status: "pending" })).toEqual({ state: "loading" });
    expect(resolveEquitySelection({ groups, status: "success" })).toEqual({
      state: "ready",
      symbol: "NVDA",
      instrument: nvda,
    });
    expect(
      resolveEquitySelection({ groups, selectedGroupId: "second", status: "success" }),
    ).toEqual({ state: "ready", symbol: "MSFT", instrument: msft });
    expect(resolveEquitySelection({ groups: [], status: "success" })).toEqual({
      state: "ready",
      symbol: "SPY",
    });
  });
});
