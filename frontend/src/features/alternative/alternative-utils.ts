import type { ETFFlowSeries, OptionContract } from "../../shared/api/generated/models";

export function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function amount(value: unknown, currency = false): string {
  if (!finite(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
    ...(currency ? { style: "currency", currency: "USD" } : {}),
  }).format(value);
}

export function decimal(value: unknown, digits = 2): string {
  return finite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: digits }) : "—";
}

export function volatility(value: unknown): string {
  return finite(value) ? `${(value * 100).toFixed(2)}%` : "—";
}

export function flowSummary(fund: ETFFlowSeries) {
  const points = [...fund.points].sort((a, b) =>
    (a.as_of_date ?? a.date).localeCompare(b.as_of_date ?? b.date),
  );
  const valid = points.filter((point) => finite(point.net_flow_usd));
  return {
    latest: points.at(-1),
    knownSum: valid.length
      ? valid.reduce((total, point) => total + (point.net_flow_usd ?? 0), 0)
      : null,
    validCount: valid.length,
    missingCount: points.length - valid.length,
  };
}

/** Keep missing IV/OI as gaps; the caller supplies a single expiry. */
export function optionCurve(
  contracts: OptionContract[],
  metric: "implied_volatility" | "open_interest",
) {
  const strikes = [...new Set(contracts.map((item) => item.strike).filter(finite))].sort(
    (a, b) => a - b,
  );
  const uniqueValue = (strike: number, type: "call" | "put") => {
    const candidates = contracts.filter(
      (item) => item.strike === strike && item.contract_type === type,
    );
    // Adjusted contracts can share a strike. Do not silently combine unlike contracts.
    return candidates.length === 1 && finite(candidates[0]?.[metric])
      ? candidates[0][metric]
      : null;
  };
  return {
    strikes,
    calls: strikes.map((strike) => uniqueValue(strike, "call")),
    puts: strikes.map((strike) => uniqueValue(strike, "put")),
  };
}

export function externalArticle(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export const qualityNames: Record<string, string> = {
  complete: "字段完整",
  partial: "部分覆盖",
  unavailable: "暂无数据",
  stale: "过期缓存",
  configuration_required: "需要配置",
  action_required: "需核对公司行动",
};
