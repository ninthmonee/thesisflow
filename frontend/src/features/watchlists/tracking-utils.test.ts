import { describe, expect, it } from "vitest";
import type { WatchlistResponse } from "../../shared/api/generated/models";
import {
  collectTrackingRows,
  isCompanyInstrument,
  readTrackingView,
  reduceTrackingSearch,
  trackingPercent,
  trackingSearchParams,
  trackingSelectionUrl,
  visibleTrackingRows,
} from "./tracking-utils";

function group(id: string, symbols: string[]): WatchlistResponse {
  return {
    id,
    name: id,
    position: 0,
    revision: 2,
    items: symbols.map((symbol, position) => ({
      id: id + ":" + symbol,
      position,
      instrument: {
        id: symbol,
        symbol,
        name: symbol === "MU" ? "Micron Technology" : symbol,
        asset_type: symbol === "SOXX" ? "etf" : "stock",
        exchange: "NASDAQ",
        currency: "USD",
        timezone: "America/New_York",
      },
      favorite: false,
      notes: "",
      observation_revision: 0,
    })),
  };
}

describe("stock tracking presentation", () => {
  it("deduplicates instruments across groups while retaining each removable relationship", () => {
    const a = group("Memory", ["MU", "SOXX"]),
      b = group("Core", ["MU"]);
    b.items[0] = { ...b.items[0]!, favorite: true, notes: "HBM供应链", observation_revision: 3 };
    const rows = collectTrackingRows([a, b]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ favorite: true, notes: "HBM供应链", observationRevision: 3 });
    expect(rows[0]?.memberships.map((row) => row.groupId)).toEqual(["Memory", "Core"]);
    expect(collectTrackingRows([a, b], "Core")).toHaveLength(1);
    expect(a.items[0]?.favorite).toBe(false);
  });
  it("filters symbol, company and shared note with favorites instead of inventing a new list", () => {
    const list = group("Memory", ["MU", "SOXX"]);
    list.items[0] = { ...list.items[0]!, favorite: true, notes: "关注HBM订单" };
    const rows = collectTrackingRows([list]);
    for (const query of ["mu", "MICRON", "hbm"]) {
      expect(
        visibleTrackingRows(rows, {
          query,
          favoritesOnly: true,
          sort: "manual",
          metrics: new Map(),
        }).map((row) => row.instrument.symbol),
      ).toEqual(["MU"]);
    }
    expect(
      visibleTrackingRows(rows, {
        query: "SOXX",
        favoritesOnly: true,
        sort: "manual",
        metrics: new Map(),
      }),
    ).toEqual([]);
  });
  it("sorts only real finite metrics and always puts unavailable values last, preserving zero", () => {
    const rows = collectTrackingRows([group("Core", ["AAA", "BBB", "CCC", "DDD"])]);
    const metrics = new Map([
      ["AAA", { change_percent: null }],
      ["BBB", { change_percent: 0 }],
      ["CCC", { change_percent: -2 }],
      ["DDD", { change_percent: 3 }],
    ]);
    const options = { query: "", favoritesOnly: false, metrics };
    expect(
      visibleTrackingRows(rows, { ...options, sort: "change_desc" }).map(
        (row) => row.instrument.symbol,
      ),
    ).toEqual(["DDD", "BBB", "CCC", "AAA"]);
    expect(
      visibleTrackingRows(rows, { ...options, sort: "change_asc" }).map(
        (row) => row.instrument.symbol,
      ),
    ).toEqual(["CCC", "BBB", "DDD", "AAA"]);
    expect(rows[0]?.instrument.symbol).toBe("AAA");
  });
  it("preserves chart identity, technical settings and list filters when selecting another symbol", () => {
    const params = new URLSearchParams(
      "symbol=MU&chart=0&interval=5m&period=1mo&indicators=sma,rsi&group=Core&favorites=1&q=HBM",
    );
    const url = new URL(
      trackingSelectionUrl(params, { id: "ETF 1", symbol: "SOXX" }, true),
      "https://local.invalid",
    );
    expect(url.pathname).toBe("/stocks/ETF%201");
    expect(url.searchParams.get("symbol")).toBe("SOXX");
    expect(url.searchParams.get("chart")).toBe("1");
    for (const key of ["interval", "period", "indicators", "group", "favorites", "q"])
      expect(url.searchParams.get(key)).toBe(params.get(key));
    expect(params.get("symbol")).toBe("MU");
    expect(readTrackingView(new URLSearchParams())).toMatchObject({ chart: true, sort: "manual" });
    expect(readTrackingView(new URLSearchParams("chart=0&sort=invalid&favorites=1"))).toMatchObject(
      { chart: false, sort: "manual", favoritesOnly: true },
    );
  });
  it("keeps company research unavailable for ETF and unknown instruments", () => {
    const rows = collectTrackingRows([group("Core", ["MU", "SOXX"])]);
    expect(isCompanyInstrument(rows[0]?.instrument)).toBe(true);
    expect(isCompanyInstrument(rows[1]?.instrument)).toBe(false);
    expect(isCompanyInstrument(undefined)).toBe(false);
  });
  it("keeps rapid Chinese and ASCII drafts intact while earlier URL commits arrive", () => {
    let state = { draft: "", urlValue: "", ownCommits: [] as string[] };
    for (const value of ["界", "界面", "界面验", "界面验收"])
      state = reduceTrackingSearch(state, { type: "type", value });
    expect(state.draft).toBe("界面验收");
    state = reduceTrackingSearch(state, { type: "url", value: "", pop: false });
    expect(state.draft).toBe("界面验收");
    state = reduceTrackingSearch(state, { type: "commit", value: state.draft });
    state = reduceTrackingSearch(state, { type: "type", value: "界面验收 HBM" });
    state = reduceTrackingSearch(state, { type: "commit", value: state.draft });
    state = reduceTrackingSearch(state, { type: "url", value: "界面验收", pop: false });
    expect(state.draft).toBe("界面验收 HBM");
    state = reduceTrackingSearch(state, { type: "url", value: "界面验收 HBM", pop: false });
    expect(state.draft).toBe("界面验收 HBM");
    state = reduceTrackingSearch(state, { type: "type", value: "" });
    state = reduceTrackingSearch(state, { type: "commit", value: "" });
    state = reduceTrackingSearch(state, { type: "url", value: "界面验收 HBM", pop: false });
    expect(state.draft).toBe("");
    state = reduceTrackingSearch(state, { type: "url", value: "旧备注", pop: true });
    expect(state).toEqual({ draft: "旧备注", urlValue: "旧备注", ownCommits: [] });
    state = reduceTrackingSearch(state, { type: "url", value: "external", pop: false });
    expect(state.draft).toBe("external");
  });
  it("commits and clears a complete search while preserving chart and list conditions", () => {
    const params = new URLSearchParams("chart=0&favorites=1&interval=5m&symbol=MU&q=old");
    const next = trackingSearchParams(params, "界面验收");
    expect(next.get("q")).toBe("界面验收");
    expect(params.get("q")).toBe("old");
    const selected = new URL(
      trackingSelectionUrl(next, { id: "SOXX", symbol: "SOXX" }, true),
      "https://local.invalid",
    );
    expect(selected.searchParams.get("q")).toBe("界面验收");
    expect(selected.searchParams.get("favorites")).toBe("1");
    expect(selected.searchParams.get("interval")).toBe("5m");
    expect(trackingSearchParams(next, "").has("q")).toBe(false);
  });
  it("distinguishes signed price changes from unsigned ATR percentage magnitudes", () => {
    expect(trackingPercent(10.04)).toBe("+10.04%");
    expect(trackingPercent(10.04, false)).toBe("10.04%");
    expect(trackingPercent(0, false)).toBe("0.00%");
    expect(trackingPercent(null, false)).toBe("—");
  });
});
