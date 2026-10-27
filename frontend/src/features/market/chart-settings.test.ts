import { describe, expect, it } from "vitest";
import type { MarketCapabilities } from "../../shared/api/generated/models";
import {
  constrainChartSettings,
  defaultChartSettings,
  readChartSettings,
  writeChartSettings,
} from "./chart-settings";

const capabilities: MarketCapabilities = {
  source: "fixture",
  feed: "history",
  intervals: ["1m", "5m", "1d"],
  periods: ["5d", "1mo", "3mo", "1y"],
  interval_periods: { "1m": ["5d"], "5m": ["5d", "1mo"], "1d": ["1mo", "3mo", "1y"] },
  sessions: ["regular", "extended"],
  adjustments: { provider_native: "来源原生", dividend_adjusted: "股息调整" },
  warning: "History only",
};

describe("shareable chart settings", () => {
  it("round trips all controls while preserving unrelated URL fields", () => {
    const settings = readChartSettings(
      new URLSearchParams(
        "interval=5m&period=1mo&session=extended&adjustment=dividend_adjusted&zone=UTC&view=line&indicators=rsi,macd&sma=50&ema=30&rsi=8&macd_fast=8&macd_slow=30&macd_signal=5&boll=15&boll_deviation=2.5&atr=12&spy=1",
      ),
    );
    const original = new URLSearchParams("symbol=BRK.B&tab=history");
    const written = writeChartSettings(original, settings);
    expect(readChartSettings(written)).toEqual(settings);
    expect(written.get("symbol")).toBe("BRK.B");
    expect(written.get("tab")).toBe("history");
    expect(original.toString()).toBe("symbol=BRK.B&tab=history");
    expect(settings.parameters).toEqual({
      sma_period: 50,
      ema_period: 30,
      rsi_period: 8,
      macd_fast: 8,
      macd_slow: 30,
      macd_signal: 5,
      boll_period: 15,
      boll_deviation: 2.5,
      atr_period: 12,
    });
  });

  it("distinguishes an explicit empty indicator list from missing or invalid preferences", () => {
    expect(readChartSettings(new URLSearchParams()).indicators).toEqual(["sma"]);
    expect(readChartSettings(new URLSearchParams("indicators=")).indicators).toEqual([]);
    expect(readChartSettings(new URLSearchParams("indicators=unknown")).indicators).toEqual([
      "sma",
    ]);
    expect(
      readChartSettings(new URLSearchParams("indicators=rsi,unknown,rsi,ema")).indicators,
    ).toEqual(["rsi", "ema"]);
  });

  it("rejects unsafe numeric values and invalid enums instead of silently extending coverage", () => {
    const parsed = readChartSettings(
      new URLSearchParams(
        "interval=2s&period=all&session=night&adjustment=raw&zone=Bad/Zone&view=pie&sma=NaN&ema=Infinity&rsi=1.5&macd_fast=101&macd_slow=201&macd_signal=0&boll=0x14&boll_deviation=5.1&atr=-10&spy=true",
      ),
    );
    expect(parsed).toEqual({
      ...defaultChartSettings,
      indicators: ["sma"],
      parameters: { ...defaultChartSettings.parameters },
    });
  });

  it("accepts declared parameter boundaries and resets a reversed MACD pair", () => {
    const parsed = readChartSettings(
      new URLSearchParams(
        "sma=2&ema=200&rsi=200&macd_fast=2&macd_slow=200&macd_signal=100&boll=200&boll_deviation=0.1&atr=2",
      ),
    );
    expect(parsed.parameters).toEqual({
      sma_period: 2,
      ema_period: 200,
      rsi_period: 200,
      macd_fast: 2,
      macd_slow: 200,
      macd_signal: 100,
      boll_period: 200,
      boll_deviation: 0.1,
      atr_period: 2,
    });
    const reversed = readChartSettings(new URLSearchParams("macd_fast=50&macd_slow=20"));
    expect(reversed.parameters.macd_fast).toBe(12);
    expect(reversed.parameters.macd_slow).toBe(26);
  });

  it("restricts minute history and daily sessions to actual capabilities", () => {
    const minute = constrainChartSettings(
      readChartSettings(new URLSearchParams("interval=1m&period=1y&session=extended")),
      capabilities,
    );
    expect(minute.interval).toBe("1m");
    expect(minute.period).toBe("5d");
    expect(minute.session).toBe("extended");
    const daily = constrainChartSettings(
      readChartSettings(new URLSearchParams("interval=1d&period=5d&session=extended")),
      capabilities,
    );
    expect(daily.period).toBe("3mo");
    expect(daily.session).toBe("regular");
  });

  it("falls back to supported interval, adjustment and session without changing the input", () => {
    const original = readChartSettings(
      new URLSearchParams("interval=1h&period=1y&session=extended&adjustment=dividend_adjusted"),
    );
    const limited: MarketCapabilities = {
      ...capabilities,
      intervals: ["5m"],
      interval_periods: { "5m": ["5d"] },
      sessions: ["regular"],
      adjustments: { provider_native: "来源原生" },
    };
    const constrained = constrainChartSettings(original, limited);
    expect(constrained.interval).toBe("5m");
    expect(constrained.period).toBe("5d");
    expect(constrained.session).toBe("regular");
    expect(constrained.adjustment).toBe("provider_native");
    expect(original.interval).toBe("1h");
  });

  it("returns independent indicator and parameter objects for each reader", () => {
    const first = readChartSettings(new URLSearchParams());
    first.indicators.push("rsi");
    first.parameters.sma_period = 60;
    const second = readChartSettings(new URLSearchParams());
    expect(second.indicators).toEqual(["sma"]);
    expect(second.parameters.sma_period).toBe(20);
    expect(defaultChartSettings.parameters.sma_period).toBe(20);
  });
});
