import type {
  GetMarketBarsParams,
  GetMarketIndicatorsParams,
  MarketCapabilities,
} from "../../shared/api/generated/models";
import { intervalChoices, isIntraday, periodChoices } from "./market-utils";
import type { ChartZone, Interval, Period } from "./market-utils";

export type IndicatorKind = "sma" | "ema" | "boll" | "rsi" | "macd" | "atr";
export type PriceView = "candles" | "line";
export type IndicatorParameters = Required<
  Pick<
    GetMarketIndicatorsParams,
    | "sma_period"
    | "ema_period"
    | "rsi_period"
    | "macd_fast"
    | "macd_slow"
    | "macd_signal"
    | "boll_period"
    | "boll_deviation"
    | "atr_period"
  >
>;
export interface ChartSettings {
  interval: Interval;
  period: Period;
  session: NonNullable<GetMarketBarsParams["session"]>;
  adjustment: NonNullable<GetMarketBarsParams["adjustment"]>;
  zone: ChartZone;
  view: PriceView;
  indicators: IndicatorKind[];
  parameters: IndicatorParameters;
  compareSpy: boolean;
}

export const defaultChartSettings = Object.freeze({
  interval: "1d",
  period: "3mo",
  session: "regular",
  adjustment: "provider_native",
  zone: "America/New_York",
  view: "candles",
  indicators: Object.freeze<IndicatorKind[]>(["sma"]),
  parameters: Object.freeze<IndicatorParameters>({
    sma_period: 20,
    ema_period: 20,
    rsi_period: 14,
    macd_fast: 12,
    macd_slow: 26,
    macd_signal: 9,
    boll_period: 20,
    boll_deviation: 2,
    atr_period: 14,
  }),
  compareSpy: false,
} satisfies Omit<ChartSettings, "indicators"> & { indicators: readonly IndicatorKind[] });

const indicatorKinds: readonly IndicatorKind[] = ["sma", "ema", "boll", "rsi", "macd", "atr"];
const parameterFields: {
  field: keyof IndicatorParameters;
  key: string;
  min: number;
  max: number;
  integer: boolean;
}[] = [
  { field: "sma_period", key: "sma", min: 2, max: 200, integer: true },
  { field: "ema_period", key: "ema", min: 2, max: 200, integer: true },
  { field: "rsi_period", key: "rsi", min: 2, max: 200, integer: true },
  { field: "macd_fast", key: "macd_fast", min: 2, max: 100, integer: true },
  { field: "macd_slow", key: "macd_slow", min: 3, max: 200, integer: true },
  { field: "macd_signal", key: "macd_signal", min: 2, max: 100, integer: true },
  { field: "boll_period", key: "boll", min: 2, max: 200, integer: true },
  { field: "boll_deviation", key: "boll_deviation", min: 0.1, max: 5, integer: false },
  { field: "atr_period", key: "atr", min: 2, max: 200, integer: true },
];

function enumValue<T extends string>(
  search: URLSearchParams,
  key: string,
  values: readonly T[],
  fallback: T,
): T {
  const candidate = search.get(key);
  return values.find((value) => value === candidate) ?? fallback;
}

export function readChartSettings(search: URLSearchParams): ChartSettings {
  const parameters = { ...defaultChartSettings.parameters };
  for (const { field, key, min, max, integer } of parameterFields) {
    const raw = search.get(key)?.trim();
    if (!raw || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)) continue;
    const number = Number(raw);
    if (
      Number.isFinite(number) &&
      number >= min &&
      number <= max &&
      (!integer || Number.isInteger(number))
    ) {
      parameters[field] = number;
    }
  }
  if (parameters.macd_fast >= parameters.macd_slow) {
    parameters.macd_fast = defaultChartSettings.parameters.macd_fast;
    parameters.macd_slow = defaultChartSettings.parameters.macd_slow;
  }
  const rawIndicators = search.get("indicators");
  const validIndicators = [
    ...new Set(
      (rawIndicators ?? "sma")
        .split(",")
        .filter((value): value is IndicatorKind => indicatorKinds.some((kind) => kind === value)),
    ),
  ];
  return {
    interval: enumValue(
      search,
      "interval",
      intervalChoices.map((item) => item.value),
      "1d",
    ),
    period: enumValue(
      search,
      "period",
      periodChoices.map((item) => item.value),
      "3mo",
    ),
    session: enumValue(search, "session", ["regular", "extended"], "regular"),
    adjustment: enumValue(
      search,
      "adjustment",
      ["provider_native", "dividend_adjusted"],
      "provider_native",
    ),
    zone: search.get("zone") === "UTC" ? "UTC" : "America/New_York",
    view: enumValue(search, "view", ["candles", "line"], "candles"),
    indicators:
      rawIndicators !== "" && validIndicators.length === 0
        ? [...defaultChartSettings.indicators]
        : validIndicators,
    parameters,
    compareSpy: search.get("spy") === "1",
  };
}

export function writeChartSettings(
  search: URLSearchParams,
  settings: ChartSettings,
): URLSearchParams {
  const next = new URLSearchParams(search);
  next.set("interval", settings.interval);
  next.set("period", settings.period);
  next.set("session", settings.session);
  next.set("adjustment", settings.adjustment);
  next.set("zone", settings.zone === "UTC" ? "UTC" : "ET");
  next.set("view", settings.view);
  next.set("indicators", settings.indicators.join(","));
  next.set("spy", settings.compareSpy ? "1" : "0");
  for (const { field, key } of parameterFields) next.set(key, String(settings.parameters[field]));
  return next;
}

/** Apply only source-declared interval/period pairs; URL parameters never expand provider coverage. */
export function constrainChartSettings(
  settings: ChartSettings,
  capabilities: MarketCapabilities,
): ChartSettings {
  const normalized = readChartSettings(writeChartSettings(new URLSearchParams(), settings));
  const supportedIntervals = intervalChoices
    .map((item) => item.value)
    .filter(
      (interval) =>
        capabilities.intervals.includes(interval) &&
        periodChoices.some((period) =>
          capabilities.interval_periods[interval]?.includes(period.value),
        ),
    );
  const interval = supportedIntervals.includes(normalized.interval)
    ? normalized.interval
    : supportedIntervals.includes("1d")
      ? "1d"
      : (supportedIntervals[0] ?? "1d");
  const supportedPeriods = periodChoices
    .map((item) => item.value)
    .filter((period) => capabilities.interval_periods[interval]?.includes(period));
  const period = supportedPeriods.includes(normalized.period)
    ? normalized.period
    : supportedPeriods.includes("3mo")
      ? "3mo"
      : (supportedPeriods[0] ?? "3mo");
  const session =
    !isIntraday(interval) || capabilities.sessions.includes(normalized.session)
      ? isIntraday(interval)
        ? normalized.session
        : "regular"
      : capabilities.sessions.includes("regular")
        ? "regular"
        : capabilities.sessions.includes("extended")
          ? "extended"
          : "regular";
  const supportedAdjustments: ChartSettings["adjustment"][] = [
    "provider_native",
    "dividend_adjusted",
  ].filter((value): value is ChartSettings["adjustment"] =>
    Object.hasOwn(capabilities.adjustments, value),
  );
  const adjustment = supportedAdjustments.includes(normalized.adjustment)
    ? normalized.adjustment
    : (supportedAdjustments[0] ?? "provider_native");
  return { ...normalized, interval, period, session, adjustment };
}
