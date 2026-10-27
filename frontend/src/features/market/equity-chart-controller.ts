import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
} from "lightweight-charts";
import type { IChartApi, ISeriesApi, MouseEventParams, SeriesType, Time } from "lightweight-charts";
import type {
  BarsResponse,
  IndicatorPoint,
  IndicatorsResponse,
} from "../../shared/api/generated/models";
import type { ChartSettings, IndicatorKind } from "./chart-settings";
import type { NormalizedComparison } from "./comparison-data";
import {
  chartTime,
  closePoint,
  finite,
  isIntraday,
  numberText,
  pricePoint,
  timestampText,
  volumePoint,
} from "./market-utils";

type IndicatorField = keyof Pick<
  IndicatorPoint,
  | "sma"
  | "ema"
  | "boll_upper"
  | "boll_middle"
  | "boll_lower"
  | "rsi"
  | "macd"
  | "macd_signal"
  | "atr"
>;
export interface ChartInput {
  data: BarsResponse;
  indicatorData?: IndicatorsResponse;
  settings: ChartSettings;
  comparison?: NormalizedComparison;
}
function timeDate(time: Time) {
  return typeof time === "number"
    ? new Date(time * 1000)
    : typeof time === "string"
      ? new Date(`${time}T12:00:00Z`)
      : new Date(Date.UTC(time.year, time.month - 1, time.day, 12));
}
function timeKey(time: Time) {
  return typeof time === "object"
    ? `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`
    : String(time);
}

/** Owns canvas lifecycle. Refreshed data updates existing series and preserves the viewport. */
export class EquityChartController {
  private readonly chart: IChartApi;
  private readonly candles: ISeriesApi<"Candlestick">;
  private readonly area: ISeriesApi<"Area">;
  private readonly volume: ISeriesApi<"Histogram">;
  private readonly legend: HTMLElement | null;
  private readonly auxiliary: ISeriesApi<SeriesType>[] = [];
  private readonly lines = new Map<IndicatorField, ISeriesApi<"Line">>();
  private histogram: ISeriesApi<"Histogram"> | undefined;
  private comparisonPrimary: ISeriesApi<"Line"> | undefined;
  private comparisonSpy: ISeriesApi<"Line"> | undefined;
  private input: ChartInput | undefined;
  private identity: string | undefined;
  private layout: string | undefined;
  private firstPoint: string | undefined;

  constructor(container: HTMLElement, legend: HTMLElement | null) {
    this.legend = legend;
    this.chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0d151f" },
        textColor: "#7f94a9",
        fontFamily: "ui-monospace, SFMono-Regular, monospace",
        fontSize: 11,
        attributionLogo: true,
        panes: { separatorColor: "#2a3744", separatorHoverColor: "#3b5266" },
      },
      grid: { vertLines: { color: "#182633" }, horzLines: { color: "#1a2835" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2a3744", minimumWidth: 68 },
      timeScale: {
        borderColor: "#2a3744",
        rightOffset: 5,
        secondsVisible: false,
        tickMarkFormatter: (time: Time, tickType: TickMarkType) => {
          const intraday = this.input && isIntraday(this.input.data.interval);
          return new Intl.DateTimeFormat("en-US", {
            timeZone: intraday ? this.input?.settings.zone : "UTC",
            ...(intraday && [TickMarkType.Time, TickMarkType.TimeWithSeconds].includes(tickType)
              ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
              : { month: "short", day: "numeric" }),
          }).format(timeDate(time));
        },
      },
      localization: {
        locale: "en-US",
        timeFormatter: (time: Time) =>
          this.input && isIntraday(this.input.data.interval)
            ? timestampText(timeDate(time).toISOString(), this.input.settings.zone)
            : timeDate(time).toISOString().slice(0, 10),
      },
    });
    this.candles = this.chart.addSeries(CandlestickSeries, {
      upColor: "#2db89d",
      downColor: "#e46579",
      borderVisible: false,
      wickUpColor: "#2db89d",
      wickDownColor: "#e46579",
    });
    this.area = this.chart.addSeries(AreaSeries, {
      visible: false,
      lineColor: "#48c4be",
      topColor: "#238d8933",
      bottomColor: "#238d8900",
      lineWidth: 2,
    });
    this.chart.addPane();
    this.volume = this.chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: "volume" }, priceLineVisible: false, title: "VOL" },
      1,
    );
    this.chart.subscribeCrosshairMove(this.onCrosshair);
  }
  private onCrosshair = (event: MouseEventParams<Time>) => {
    const input = this.input,
      time = event.time;
    if (!input || !this.legend) return;
    const bar = time
      ? input.data.bars.find(
          (item) => timeKey(chartTime(item, input.data.interval)) === timeKey(time),
        )
      : undefined;
    this.legend.textContent = bar
      ? `${isIntraday(input.data.interval) ? timestampText(bar.timestamp, input.settings.zone) : bar.date}  O ${numberText(bar.open)}  H ${numberText(bar.high)}  L ${numberText(bar.low)}  C ${numberText(bar.close)}  VOL ${numberText(bar.volume, 0)}  ${bar.session === "regular" ? "常规" : bar.session === "pre" ? "盘前" : "盘后"}${bar.is_complete ? "" : " · 未完成K线"}`
      : "移动十字光标查看 OHLC / 成交量 · 滚轮缩放 · 拖拽平移";
  };
  private addLine(field: IndicatorField, title: string, color: string, pane = 0) {
    const series = this.chart.addSeries(
      LineSeries,
      { title, color, lineWidth: 1, priceLineVisible: false, lastValueVisible: pane > 0 },
      pane,
    );
    this.lines.set(field, series);
    this.auxiliary.push(series);
    return series;
  }
  private rebuildAuxiliary(indicators: IndicatorKind[], compare: boolean) {
    for (const series of this.auxiliary) this.chart.removeSeries(series);
    this.auxiliary.length = 0;
    this.lines.clear();
    this.histogram = undefined;
    this.comparisonPrimary = undefined;
    this.comparisonSpy = undefined;
    while (this.chart.panes().length > 2) this.chart.removePane(this.chart.panes().length - 1);
    if (indicators.includes("sma")) this.addLine("sma", "SMA", "#e6b864");
    if (indicators.includes("ema")) this.addLine("ema", "EMA", "#83a5f5");
    if (indicators.includes("boll")) {
      this.addLine("boll_upper", "BOLL U", "#a194cc");
      this.addLine("boll_middle", "BOLL M", "#7e719e");
      this.addLine("boll_lower", "BOLL L", "#a194cc");
    }
    let paneIndex = 1;
    if (indicators.includes("rsi")) {
      this.chart.addPane();
      const rsi = this.addLine("rsi", "RSI", "#bba2ec", ++paneIndex);
      for (const price of [30, 70])
        rsi.createPriceLine({
          price,
          color: "#54536f",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "",
        });
    }
    if (indicators.includes("macd")) {
      this.chart.addPane();
      ++paneIndex;
      this.addLine("macd", "MACD", "#79a9e6", paneIndex);
      this.addLine("macd_signal", "SIGNAL", "#e6b864", paneIndex);
      this.histogram = this.chart.addSeries(
        HistogramSeries,
        { title: "MACD HIST", priceLineVisible: false },
        paneIndex,
      );
      this.auxiliary.push(this.histogram);
    }
    if (indicators.includes("atr")) {
      this.chart.addPane();
      this.addLine("atr", "ATR", "#d49f70", ++paneIndex);
    }
    if (compare) {
      this.chart.addPane();
      ++paneIndex;
      this.comparisonPrimary = this.chart.addSeries(
        LineSeries,
        {
          title: "股票 %",
          color: "#4fc9c0",
          lineWidth: 2,
          priceLineVisible: false,
          priceFormat: { type: "custom", formatter: (value: number) => `${value.toFixed(2)}%` },
        },
        paneIndex,
      );
      this.comparisonSpy = this.chart.addSeries(
        LineSeries,
        {
          title: "SPY %",
          color: "#e9ba6b",
          lineWidth: 2,
          priceLineVisible: false,
          priceFormat: { type: "custom", formatter: (value: number) => `${value.toFixed(2)}%` },
        },
        paneIndex,
      );
      this.auxiliary.push(this.comparisonPrimary, this.comparisonSpy);
    }
    this.chart.panes().forEach((pane, index) => pane.setHeight(index === 0 ? 340 : 110));
  }
  synchronize(input: ChartInput) {
    const beforeLogical = this.chart.timeScale().getVisibleLogicalRange(),
      beforeTime = this.chart.timeScale().getVisibleRange();
    const identity = JSON.stringify([
      input.data.symbol,
      input.data.interval,
      input.data.session,
      input.data.adjustment,
      input.data.meta.source,
      input.data.meta.feed,
    ]);
    const changedIdentity = this.identity !== identity;
    const nextFirst = input.data.bars[0]
      ? timeKey(chartTime(input.data.bars[0], input.data.interval))
      : undefined;
    const layout = JSON.stringify([input.settings.indicators, input.settings.compareSpy]);
    this.input = input;
    this.chart.applyOptions({ timeScale: { timeVisible: isIntraday(input.data.interval) } });
    if (this.layout !== layout) {
      this.rebuildAuxiliary(input.settings.indicators, input.settings.compareSpy);
      this.layout = layout;
    }
    this.candles.applyOptions({ visible: input.settings.view === "candles" });
    this.area.applyOptions({ visible: input.settings.view === "line" });
    this.candles.setData(input.data.bars.map((bar) => pricePoint(bar, input.data.interval)));
    this.area.setData(input.data.bars.map((bar) => closePoint(bar, input.data.interval)));
    this.volume.setData(input.data.bars.map((bar) => volumePoint(bar, input.data.interval)));
    for (const [field, series] of this.lines) {
      const title =
        field === "sma"
          ? `SMA${input.settings.parameters.sma_period}`
          : field === "ema"
            ? `EMA${input.settings.parameters.ema_period}`
            : field === "rsi"
              ? `RSI${input.settings.parameters.rsi_period}`
              : field === "atr"
                ? `ATR${input.settings.parameters.atr_period}`
                : field === "macd"
                  ? `MACD${input.settings.parameters.macd_fast},${input.settings.parameters.macd_slow}`
                  : field === "macd_signal"
                    ? `SIGNAL${input.settings.parameters.macd_signal}`
                    : `BOLL${input.settings.parameters.boll_period},${input.settings.parameters.boll_deviation}`;
      series.applyOptions({ title });
      series.setData(
        (input.indicatorData?.points ?? []).map((point) => {
          const time = chartTime(point, input.data.interval),
            value = point[field];
          return finite(value) ? { time, value } : { time };
        }),
      );
    }
    this.histogram?.setData(
      (input.indicatorData?.points ?? []).map((point) => {
        const time = chartTime(point, input.data.interval),
          value = point.macd_histogram;
        return finite(value)
          ? { time, value, color: value >= 0 ? "#217d70" : "#a4495a" }
          : { time };
      }),
    );
    this.comparisonPrimary?.applyOptions({ title: `${input.data.symbol} %` });
    this.comparisonPrimary?.setData(input.comparison?.primary ?? []);
    this.comparisonSpy?.setData(input.comparison?.benchmark ?? []);
    if (changedIdentity || !beforeLogical) this.chart.timeScale().fitContent();
    else if (this.firstPoint !== nextFirst && beforeTime)
      this.chart.timeScale().setVisibleRange(beforeTime);
    else this.chart.timeScale().setVisibleLogicalRange(beforeLogical);
    this.identity = identity;
    this.firstPoint = nextFirst;
  }
  fitContent() {
    this.chart.timeScale().fitContent();
  }
  dispose() {
    this.chart.unsubscribeCrosshairMove(this.onCrosshair);
    this.chart.remove();
  }
}
