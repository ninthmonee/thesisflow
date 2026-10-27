import { describe, expect, it } from "vitest";
import type { BarsResponse, MarketBar } from "../../shared/api/generated/models";
import { buildComparison } from "./comparison-data";

function bar(date: string, close: number | null, complete = true): MarketBar {
  return {
    date,
    timestamp: `${date}T00:00:00Z`,
    open: close,
    high: close,
    low: close,
    close,
    volume: 100,
    is_complete: complete,
    session: "regular",
  };
}
function data(
  symbol: string,
  bars: MarketBar[],
  overrides: Partial<BarsResponse> = {},
): BarsResponse {
  return {
    symbol,
    bars,
    interval: "1d",
    period: "3mo",
    session: "regular",
    adjustment: "provider_native",
    adjustment_label: "原生口径",
    meta: {
      source: "fixture",
      feed: "same-history",
      as_of: null,
      received_at: "2026-10-23T20:30:00Z",
      cache: "fresh",
      quality: "complete",
    },
    ...overrides,
  };
}

describe("paired benchmark normalization", () => {
  it("starts at the first common completed bar rather than each series own start", () => {
    const comparison = buildComparison(
      data("NVDA", [bar("2026-10-19", 50), bar("2026-10-20", 100), bar("2026-10-21", 110)]),
      data("SPY", [bar("2026-10-20", 200), bar("2026-10-21", 190), bar("2026-10-22", 210)]),
    );
    expect(comparison.reason).toBeNull();
    expect(comparison.start).toBe("2026-10-20");
    expect(comparison.end).toBe("2026-10-21");
    expect(comparison.commonCount).toBe(2);
    expect(comparison.primaryCount).toBe(3);
    expect(comparison.benchmarkCount).toBe(3);
    expect(comparison.primary[0]).toEqual({ time: "2026-10-20", value: 0 });
    expect(comparison.primary[1]).toHaveProperty("value", expect.closeTo(10));
    expect(comparison.benchmark[1]).toHaveProperty("value", expect.closeTo(-5));
  });

  it("keeps missing, incomplete and invalid interior points blank on both curves", () => {
    const comparison = buildComparison(
      data("NVDA", [
        bar("2026-10-19", 100),
        bar("2026-10-20", 105),
        bar("2026-10-21", 108, false),
        bar("2026-10-22", 0),
        bar("2026-10-23", 110),
      ]),
      data("SPY", [
        bar("2026-10-19", 200),
        bar("2026-10-21", 205),
        bar("2026-10-22", 210),
        bar("2026-10-23", 220),
      ]),
    );
    expect(comparison.commonCount).toBe(2);
    for (const curve of [comparison.primary, comparison.benchmark]) {
      expect(curve[1]).toEqual({ time: "2026-10-20" });
      expect(curve[2]).toEqual({ time: "2026-10-21" });
      expect(curve[3]).toEqual({ time: "2026-10-22" });
    }
    expect(comparison.primaryCount).toBe(3);
    expect(comparison.benchmarkCount).toBe(4);
  });

  it("preserves a true zero-percent return as a value", () => {
    const comparison = buildComparison(
      data("NVDA", [bar("2026-10-22", 100), bar("2026-10-23", 100)]),
      data("SPY", [bar("2026-10-22", 200), bar("2026-10-23", 200)]),
    );
    expect(comparison.primary).toEqual([
      { time: "2026-10-22", value: 0 },
      { time: "2026-10-23", value: 0 },
    ]);
    expect(comparison.benchmark).toEqual(comparison.primary);
  });

  it.each(["source", "feed", "interval", "session", "adjustment", "period"] as const)(
    "rejects incompatible %s metadata instead of drawing a comparison",
    (field) => {
      const primary = data("NVDA", [bar("2026-10-22", 100), bar("2026-10-23", 110)]);
      const benchmark = data("SPY", [bar("2026-10-22", 200), bar("2026-10-23", 210)]);
      if (field === "source") benchmark.meta.source = "different";
      if (field === "feed") benchmark.meta.feed = "different";
      if (field === "interval") benchmark.interval = "5m";
      if (field === "session") benchmark.session = "extended";
      if (field === "adjustment") benchmark.adjustment = "dividend_adjusted";
      if (field === "period") benchmark.period = "1y";
      const comparison = buildComparison(primary, benchmark);
      expect(comparison.reason).toBeTypeOf("string");
      expect(comparison.primary).toEqual([]);
      expect(comparison.benchmark).toEqual([]);
      expect(comparison.start).toBeNull();
      expect(comparison.end).toBeNull();
    },
  );

  it("aligns intraday timezone representations by their UTC instant", () => {
    const first = { ...bar("2026-10-23", 100), timestamp: "2026-10-23T09:30:00-04:00" };
    const second = { ...bar("2026-10-23", 105), timestamp: "2026-10-23T09:35:00-04:00" };
    const comparison = buildComparison(
      data("NVDA", [first, second], { interval: "5m", period: "5d" }),
      data(
        "SPY",
        [
          { ...first, timestamp: "2026-10-23T13:30:00Z", close: 200 },
          { ...second, timestamp: "2026-10-23T13:35:00Z", close: 220 },
        ],
        { interval: "5m", period: "5d" },
      ),
    );
    expect(comparison.commonCount).toBe(2);
    expect(comparison.start).toBe("2026-10-23T13:30:00.000Z");
    expect(comparison.end).toBe("2026-10-23T13:35:00.000Z");
    expect(comparison.primary[0]).toEqual({ time: Date.parse(first.timestamp) / 1000, value: 0 });
  });

  it("does not manufacture a shared start from invalid or uncompleted coverage", () => {
    const comparison = buildComparison(
      data("NVDA", [
        bar("2026-10-21", null),
        bar("2026-10-22", 100),
        bar("2026-10-23", 110, false),
      ]),
      data("SPY", [bar("2026-10-21", 200), bar("2026-10-22", 210), bar("2026-10-23", -1)]),
    );
    expect(comparison.reason).toContain("覆盖不足");
    expect(comparison.commonCount).toBe(1);
    expect(comparison.primary).toEqual([]);
    expect(comparison.benchmark).toEqual([]);
    expect(comparison.primaryCount).toBe(1);
    expect(comparison.benchmarkCount).toBe(2);
  });
});
