import type { Time, UTCTimestamp } from "lightweight-charts";
import { ApiError } from "../../shared/api/transport";
import type {
  GetMarketBarsParams,
  MarketBar,
  MarketCapabilities,
  MarketMeta,
  SectorMetric,
} from "../../shared/api/generated/models";

export type Interval = NonNullable<GetMarketBarsParams["interval"]>;
export type Period = NonNullable<GetMarketBarsParams["period"]>;
export type ChartZone = "America/New_York" | "UTC";
export function marketFailure(error: unknown) {
  return error instanceof ApiError ? error.message : "数据格式或连接异常，请重试或检查服务版本。";
}
export const intervalChoices: { value: Interval; label: string }[] = [
  { value: "1m", label: "1 分" },
  { value: "5m", label: "5 分" },
  { value: "15m", label: "15 分" },
  { value: "30m", label: "30 分" },
  { value: "1h", label: "1 小时" },
  { value: "1d", label: "日 K" },
  { value: "1wk", label: "周 K" },
  { value: "1mo", label: "月 K" },
];
export const periodChoices: { value: Period; label: string }[] = [
  { value: "5d", label: "5 交易日" },
  { value: "1mo", label: "1 个月" },
  { value: "3mo", label: "3 个月" },
  { value: "6mo", label: "6 个月" },
  { value: "1y", label: "1 年" },
  { value: "2y", label: "2 年" },
  { value: "5y", label: "5 年" },
];

export const qualityLabels: Record<string, string> = {
  complete: "覆盖完整",
  partial: "部分数据",
  stale: "过期数据",
  unavailable: "数据不可用",
};

export function isIntraday(interval: Interval) {
  return !["1d", "1wk", "1mo"].includes(interval);
}

export function availablePeriods(interval: Interval, capabilities: MarketCapabilities): Period[] {
  const supported = capabilities.interval_periods[interval] ?? [];
  return periodChoices.map((item) => item.value).filter((value) => supported.includes(value));
}

export function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function numberText(value: number | null | undefined, digits = 2) {
  return finite(value)
    ? value.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";
}

export function signedText(value: number | null | undefined, suffix = "%") {
  return finite(value) ? `${value > 0 ? "+" : ""}${numberText(value)}${suffix}` : "—";
}

export function compactText(value: number | null | undefined) {
  return finite(value)
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(
        value,
      )
    : "—";
}

export function timestampText(value: string | null | undefined, zone: ChartZone) {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.valueOf())) return "—";
  return `${new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date)} ${zone === "UTC" ? "UTC" : "ET"}`;
}

export function marketAsOfText(meta: Pick<MarketMeta, "as_of_kind" | "as_of">, zone: ChartZone) {
  return meta.as_of_kind === "trading_date"
    ? (meta.as_of?.slice(0, 10) ?? "—")
    : timestampText(meta.as_of, zone);
}

export function chartTime(bar: { date: string; timestamp: string }, interval: Interval): Time {
  return isIntraday(interval) ? ((Date.parse(bar.timestamp) / 1000) as UTCTimestamp) : bar.date;
}

export function pricePoint(bar: MarketBar, interval: Interval) {
  const time = chartTime(bar, interval);
  return finite(bar.open) && finite(bar.high) && finite(bar.low) && finite(bar.close)
    ? { time, open: bar.open, high: bar.high, low: bar.low, close: bar.close }
    : { time };
}

export function closePoint(bar: MarketBar, interval: Interval) {
  const time = chartTime(bar, interval);
  return finite(bar.close) ? { time, value: bar.close } : { time };
}

export function volumePoint(bar: MarketBar, interval: Interval) {
  const time = chartTime(bar, interval);
  return finite(bar.volume)
    ? {
        time,
        value: bar.volume,
        color:
          !finite(bar.open) || !finite(bar.close)
            ? "#506172"
            : bar.close >= bar.open
              ? "#217d70"
              : "#a4495a",
      }
    : { time };
}

export function datedWindow(today: string | null | undefined) {
  if (!today) return undefined;
  const date = new Date(`${today.slice(0, 10)}T12:00:00Z`);
  if (!Number.isFinite(date.valueOf())) return undefined;
  return {
    start: new Date(date.valueOf() - 3 * 86400_000).toISOString().slice(0, 10),
    end: new Date(date.valueOf() + 7 * 86400_000).toISOString().slice(0, 10),
  };
}

export function sectorHeatData(sectors: SectorMetric[]) {
  return sectors.flatMap((sector) =>
    finite(sector.return_percent)
      ? [{ name: sector.symbol, value: [sector.return_percent], sector }]
      : [],
  );
}
