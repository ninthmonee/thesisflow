import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import type { Time } from "lightweight-charts";
import type { BarsResponse, MarketBar } from "../../shared/api/generated/models";
import type { ChartSettings } from "./chart-settings";
import type { ChartInput } from "./equity-chart-controller";

interface MockSeries {
  setData: Mock<(data: readonly unknown[]) => void>;
  applyOptions: Mock<(options: unknown) => void>;
  createPriceLine: Mock<(options: unknown) => void>;
}
interface LogicalRange {
  from: number;
  to: number;
}
interface TimeRange {
  from: Time;
  to: Time;
}

const harness = vi.hoisted(() => {
  function series(): MockSeries {
    return {
      setData: vi.fn<(data: readonly unknown[]) => void>(),
      applyOptions: vi.fn<(options: unknown) => void>(),
      createPriceLine: vi.fn<(options: unknown) => void>(),
    };
  }
  function pane() {
    return { setHeight: vi.fn<(height: number) => void>() };
  }
  function createMockChart() {
    const viewport: { logical: LogicalRange | null; time: TimeRange | null } = {
      logical: null,
      time: null,
    };
    const timeScale = {
      getVisibleLogicalRange: vi.fn<() => LogicalRange | null>(() => viewport.logical),
      getVisibleRange: vi.fn<() => TimeRange | null>(() => viewport.time),
      setVisibleLogicalRange: vi.fn<(range: LogicalRange) => void>(),
      setVisibleRange: vi.fn<(range: TimeRange) => void>(),
      fitContent: vi.fn<() => void>(),
    };
    const panes = [pane()];
    const seriesList: MockSeries[] = [];
    return {
      viewport,
      series: seriesList,
      timeScale: vi.fn<() => typeof timeScale>(() => timeScale),
      applyOptions: vi.fn<(options: unknown) => void>(),
      addSeries: vi.fn<(definition: unknown, options?: unknown, paneIndex?: number) => MockSeries>(
        () => {
          const item = series();
          seriesList.push(item);
          return item;
        },
      ),
      removeSeries: vi.fn<(item: MockSeries) => void>(),
      addPane: vi.fn<() => ReturnType<typeof pane>>(() => {
        const item = pane();
        panes.push(item);
        return item;
      }),
      removePane: vi.fn<(index: number) => void>((index) => {
        panes.splice(index, 1);
      }),
      panes: vi.fn<() => ReturnType<typeof pane>[]>(() => panes),
      subscribeCrosshairMove: vi.fn<(listener: unknown) => void>(),
      unsubscribeCrosshairMove: vi.fn<(listener: unknown) => void>(),
      remove: vi.fn<() => void>(),
    };
  }
  const instances: ReturnType<typeof createMockChart>[] = [];
  return {
    instances,
    createChart: vi.fn<() => ReturnType<typeof createMockChart>>(() => {
      const chart = createMockChart();
      instances.push(chart);
      return chart;
    }),
  };
});

vi.mock("lightweight-charts", () => ({
  createChart: harness.createChart,
  AreaSeries: "area",
  CandlestickSeries: "candles",
  HistogramSeries: "histogram",
  LineSeries: "line",
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Dashed: 2 },
  TickMarkType: { Time: 3, TimeWithSeconds: 4 },
}));

import { readChartSettings } from "./chart-settings";
import { EquityChartController } from "./equity-chart-controller";

function bar(date: string, close: number): MarketBar {
  return {
    date,
    timestamp: `${date}T13:30:00Z`,
    open: close - 1,
    high: close + 1,
    low: close - 2,
    close,
    volume: 100,
    is_complete: true,
    session: "regular",
  };
}
function input(
  overrides: Partial<BarsResponse> = {},
  settings: Partial<ChartSettings> = {},
): ChartInput {
  return {
    data: {
      symbol: "NVDA",
      interval: "1d",
      period: "3mo",
      session: "regular",
      adjustment: "provider_native",
      adjustment_label: "来源原生",
      bars: [bar("2026-10-21", 100), bar("2026-10-22", 105), bar("2026-10-23", 110)],
      meta: {
        source: "fixture",
        feed: "same-history",
        cache: "fresh",
        quality: "complete",
        as_of: "2026-10-23T00:00:00Z",
        received_at: "2026-10-23T20:30:00Z",
      },
      ...overrides,
    },
    settings: { ...readChartSettings(new URLSearchParams("indicators=sma,rsi,macd")), ...settings },
  };
}
function setup() {
  const controller = new EquityChartController({} as HTMLElement, null);
  const chart = harness.instances[0];
  if (!chart) throw new Error("Chart fixture was not initialized");
  return { controller, chart, scale: chart.timeScale() };
}

describe("persistent chart viewport", () => {
  beforeEach(() => {
    harness.instances.length = 0;
    harness.createChart.mockClear();
  });

  it("updates refreshed data in existing series while preserving the zoomed logical range", () => {
    const { controller, chart, scale } = setup();
    controller.synchronize(input());
    const additions = chart.addSeries.mock.calls.length;
    const candles = chart.series[0];
    if (!candles) throw new Error("Price series fixture was not initialized");
    const zoom = { from: 0.5, to: 1.75 };
    chart.viewport.logical = zoom;
    chart.viewport.time = { from: "2026-10-21", to: "2026-10-23" };
    controller.synchronize(
      input({ bars: [bar("2026-10-21", 100), bar("2026-10-22", 105), bar("2026-10-23", 112)] }),
    );
    expect(harness.createChart).toHaveBeenCalledTimes(1);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(candles.setData).toHaveBeenCalledTimes(2);
    expect(candles.setData).toHaveBeenLastCalledWith(
      expect.arrayContaining([expect.objectContaining({ time: "2026-10-23", close: 112 })]),
    );
    expect(chart.addSeries).toHaveBeenCalledTimes(additions);
    expect(chart.removeSeries).not.toHaveBeenCalled();
    expect(scale.fitContent).toHaveBeenCalledTimes(1);
    expect(scale.setVisibleLogicalRange).toHaveBeenLastCalledWith(zoom);
    expect(scale.setVisibleRange).not.toHaveBeenCalled();
  });

  it.each([
    { label: "symbol", change: { symbol: "MSFT" } },
    { label: "interval", change: { interval: "1h" } },
    { label: "session", change: { session: "extended" } },
    { label: "adjustment", change: { adjustment: "dividend_adjusted" } },
  ] satisfies { label: string; change: Partial<BarsResponse> }[])(
    "fits a genuinely changed $label identity",
    ({ change }) => {
      const { controller, chart, scale } = setup();
      controller.synchronize(input({ interval: "5m", period: "5d" }));
      chart.viewport.logical = { from: 1, to: 2 };
      chart.viewport.time = { from: "2026-10-22", to: "2026-10-23" };
      controller.synchronize(input({ interval: "5m", period: "5d", ...change }));
      expect(harness.createChart).toHaveBeenCalledTimes(1);
      expect(scale.fitContent).toHaveBeenCalledTimes(2);
      expect(scale.setVisibleLogicalRange).not.toHaveBeenCalled();
      expect(scale.setVisibleRange).not.toHaveBeenCalled();
    },
  );

  it("keeps visible timestamps when extending the history range prepends earlier bars", () => {
    const { controller, chart, scale } = setup();
    const initial = input();
    controller.synchronize(initial);
    const timeAnchor = { from: "2026-10-22", to: "2026-10-23" };
    chart.viewport.logical = { from: 1, to: 2 };
    chart.viewport.time = timeAnchor;
    controller.synchronize(
      input({ period: "1y", bars: [bar("2026-10-01", 90), ...initial.data.bars] }),
    );
    expect(scale.fitContent).toHaveBeenCalledTimes(1);
    expect(scale.setVisibleRange).toHaveBeenLastCalledWith(timeAnchor);
    expect(scale.setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("retains auxiliary series when only indicator parameters and display preferences change", () => {
    const { controller, chart, scale } = setup();
    const initial = input();
    controller.synchronize(initial);
    const additions = chart.addSeries.mock.calls.length;
    chart.viewport.logical = { from: 1, to: 2 };
    controller.synchronize({
      ...initial,
      settings: {
        ...initial.settings,
        view: "line",
        zone: "UTC",
        parameters: { ...initial.settings.parameters, sma_period: 50 },
      },
    });
    expect(chart.addSeries).toHaveBeenCalledTimes(additions);
    expect(chart.removeSeries).not.toHaveBeenCalled();
    expect(scale.fitContent).toHaveBeenCalledTimes(1);
    expect(scale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 1, to: 2 });
  });

  it("unsubscribes its original crosshair handler and releases the chart on disposal", () => {
    const { controller, chart } = setup();
    controller.synchronize(input());
    const listener = chart.subscribeCrosshairMove.mock.calls[0]?.[0];
    expect(listener).toBeTypeOf("function");
    controller.dispose();
    expect(chart.unsubscribeCrosshairMove).toHaveBeenCalledExactlyOnceWith(listener);
    expect(chart.remove).toHaveBeenCalledTimes(1);
  });
});
