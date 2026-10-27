import type { LineData, Time, WhitespaceData } from "lightweight-charts";
import type { BarsResponse } from "../../shared/api/generated/models";
import { chartTime, finite, isIntraday } from "./market-utils";

export interface NormalizedComparison {
  primarySymbol: string;
  primary: (LineData<Time> | WhitespaceData<Time>)[];
  benchmark: (LineData<Time> | WhitespaceData<Time>)[];
  start: string | null;
  end: string | null;
  commonCount: number;
  primaryCount: number;
  benchmarkCount: number;
  reason: string | null;
}

interface AlignedPoint {
  time: Time;
  order: number;
  label: string;
  close: number | null;
  complete: boolean;
}

function alignedBars(data: BarsResponse): Map<number, AlignedPoint> {
  const result = new Map<number, AlignedPoint>();
  for (const bar of data.bars) {
    const intraday = isIntraday(data.interval);
    const order = Date.parse(intraday ? bar.timestamp : `${bar.date}T00:00:00Z`);
    if (!Number.isFinite(order)) continue;
    const label = intraday ? new Date(order).toISOString() : bar.date;
    if (
      !intraday &&
      (!/^\d{4}-\d{2}-\d{2}$/.test(label) || new Date(order).toISOString().slice(0, 10) !== label)
    )
      continue;
    result.set(order, {
      time: chartTime(bar, data.interval),
      order,
      label,
      close: bar.close,
      complete: bar.is_complete,
    });
  }
  return result;
}

function completePrice(point: AlignedPoint | undefined): point is AlignedPoint & { close: number } {
  return !!point && point.complete && finite(point.close) && point.close > 0;
}

function mismatch(primary: BarsResponse, benchmark: BarsResponse) {
  if (primary.meta.source !== benchmark.meta.source) return "来源不一致，不能进行同口径比较。";
  if (primary.meta.feed !== benchmark.meta.feed) return "Feed 不一致，不能进行同口径比较。";
  if (primary.interval !== benchmark.interval) return "K 线周期不一致，不能进行同口径比较。";
  if (primary.session !== benchmark.session) return "交易时段不一致，不能进行同口径比较。";
  if (primary.adjustment !== benchmark.adjustment)
    return "价格调整口径不一致，不能进行同口径比较。";
  if (primary.period !== benchmark.period) return "历史范围不一致，不能进行同口径比较。";
  return null;
}

/** Normalize only paired, completed close prices, retaining gaps on both curves. */
export function buildComparison(
  primary: BarsResponse,
  benchmark: BarsResponse,
): NormalizedComparison {
  const primaryBars = alignedBars(primary),
    benchmarkBars = alignedBars(benchmark);
  const primaryCount = [...primaryBars.values()].filter(completePrice).length;
  const benchmarkCount = [...benchmarkBars.values()].filter(completePrice).length;
  function unavailable(reason: string, commonCount = 0): NormalizedComparison {
    return {
      primarySymbol: primary.symbol,
      primary: [],
      benchmark: [],
      start: null,
      end: null,
      commonCount,
      primaryCount,
      benchmarkCount,
      reason,
    };
  }
  const incompatibility = mismatch(primary, benchmark);
  if (incompatibility) return unavailable(incompatibility);
  const common = [...primaryBars.entries()]
    .sort(([first], [second]) => first - second)
    .flatMap(([order, point]) => {
      const other = benchmarkBars.get(order);
      return completePrice(point) && completePrice(other)
        ? [{ order, primary: point, benchmark: other }]
        : [];
    });
  const first = common[0],
    last = common[common.length - 1];
  if (common.length < 2 || !first || !last)
    return unavailable("完整有效的共同 K 线不足 2 个，比较覆盖不足。", common.length);
  const commonByTime = new Map(common.map((point) => [point.order, point]));
  const timeline = [...new Set([...primaryBars.keys(), ...benchmarkBars.keys()])]
    .filter((order) => order >= first.order && order <= last.order)
    .sort((a, b) => a - b);
  const primaryCurve: NormalizedComparison["primary"] = [],
    benchmarkCurve: NormalizedComparison["benchmark"] = [];
  let commonCount = 0;
  for (const order of timeline) {
    const pair = commonByTime.get(order);
    const time = primaryBars.get(order)?.time ?? benchmarkBars.get(order)?.time;
    if (time === undefined) continue;
    const primaryValue = pair ? (pair.primary.close / first.primary.close - 1) * 100 : undefined;
    const benchmarkValue = pair
      ? (pair.benchmark.close / first.benchmark.close - 1) * 100
      : undefined;
    if (finite(primaryValue) && finite(benchmarkValue)) {
      primaryCurve.push({ time, value: primaryValue });
      benchmarkCurve.push({ time, value: benchmarkValue });
      ++commonCount;
    } else {
      primaryCurve.push({ time });
      benchmarkCurve.push({ time });
    }
  }
  if (commonCount < 2)
    return unavailable("可归一化的共同 K 线不足 2 个，比较覆盖不足。", commonCount);
  return {
    primarySymbol: primary.symbol,
    primary: primaryCurve,
    benchmark: benchmarkCurve,
    start: first.primary.label,
    end: last.primary.label,
    commonCount,
    primaryCount,
    benchmarkCount,
    reason: null,
  };
}
