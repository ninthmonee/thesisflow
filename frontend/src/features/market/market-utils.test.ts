import { describe, expect, it } from "vitest";
import type { MarketBar, SectorMetric } from "../../shared/api/generated/models";
import {
  chartTime,
  closePoint,
  numberText,
  marketAsOfText,
  pricePoint,
  sectorHeatData,
  timestampText,
  volumePoint,
} from "./market-utils";

const bar: MarketBar = {
  timestamp: "2026-10-23T13:30:00Z",
  date: "2026-10-23",
  open: 10,
  high: 12,
  low: 9,
  close: 11,
  volume: 100,
  session: "regular",
  is_complete: true,
};

describe("market display data integrity", () => {
  it("keeps missing OHLC and volume as chart whitespace rather than zero", () => {
    expect(pricePoint({ ...bar, high: null }, "5m")).toEqual({ time: 1792762200 });
    expect(volumePoint({ ...bar, volume: null }, "5m")).toEqual({ time: 1792762200 });
    expect(closePoint({ ...bar, close: null }, "1d")).toEqual({ time: "2026-10-23" });
    expect(numberText(null)).toBe("—");
    expect(numberText(0)).toBe("0.00");
  });

  it("uses exchange session dates for daily bars and UTC instants for intraday bars", () => {
    expect(chartTime(bar, "1d")).toBe("2026-10-23");
    expect(chartTime(bar, "5m")).toBe(Date.parse(bar.timestamp) / 1000);
    expect(timestampText(bar.timestamp, "America/New_York")).toContain("09:30:00 ET");
    expect(timestampText("2026-12-23T14:30:00Z", "America/New_York")).toContain("09:30:00 ET");
    expect(
      marketAsOfText(
        { as_of_kind: "trading_date", as_of: "2026-10-23T00:00:00Z" },
        "America/New_York",
      ),
    ).toBe("2026-10-23");
  });

  it("does not render unavailable sector returns as neutral performance", () => {
    const sectors = [
      { symbol: "XLK", return_percent: null },
      { symbol: "XLE", return_percent: 0 },
    ] as SectorMetric[];
    expect(sectorHeatData(sectors).map((point) => point.name)).toEqual(["XLE"]);
  });
});
