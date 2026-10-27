import type { InstrumentResponse, WatchlistResponse } from "../../shared/api/generated/models";

export const trackingSortLabels = {
  manual: "分组原顺序",
  symbol: "代码 A–Z",
  change_desc: "涨跌幅从高到低",
  change_asc: "涨跌幅从低到高",
  price_desc: "收盘价从高到低",
  volatility_desc: "技术波动从高到低",
};
export type TrackingSort = keyof typeof trackingSortLabels;

export interface TrackingSearchState {
  draft: string;
  urlValue: string;
  ownCommits: string[];
}
export type TrackingSearchAction =
  | { type: "type"; value: string }
  | { type: "commit"; value: string }
  | { type: "url"; value: string; pop: boolean };

/** Local input stays authoritative while our asynchronous router commits settle. */
export function reduceTrackingSearch(
  state: TrackingSearchState,
  action: TrackingSearchAction,
): TrackingSearchState {
  if (action.type === "type")
    return state.draft === action.value ? state : { ...state, draft: action.value };
  if (action.type === "commit") {
    const ownCommits = [
      ...state.ownCommits.filter((value) => value !== action.value),
      action.value,
    ].slice(-16);
    return { ...state, ownCommits };
  }
  const preserveDraft =
    !action.pop && (action.value === state.urlValue || state.ownCommits.includes(action.value));
  return {
    draft: preserveDraft ? state.draft : action.value,
    urlValue: action.value,
    ownCommits: action.pop ? [] : state.ownCommits,
  };
}

export function trackingSearchParams(params: URLSearchParams, draft: string) {
  const next = new URLSearchParams(params);
  if (draft) next.set("q", draft);
  else next.delete("q");
  return next;
}

export function trackingPercent(value: number | null | undefined, signed = true) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${signed && value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export interface TrackingMembership {
  groupId: string;
  groupName: string;
  groupRevision: number;
  itemId: string;
  position: number;
}
export interface TrackingRow {
  instrument: InstrumentResponse;
  memberships: TrackingMembership[];
  favorite: boolean;
  notes: string;
  observationRevision: number;
}
export interface TrackingSortMetric {
  price?: number | null;
  change_percent?: number | null;
  technical_volatility_level?: number | null;
}

export function collectTrackingRows(groups: WatchlistResponse[], groupId?: string): TrackingRow[] {
  const rows = new Map<string, TrackingRow>();
  for (const group of groups) {
    if (groupId && group.id !== groupId) continue;
    for (const item of group.items) {
      const relation = {
        groupId: group.id,
        groupName: group.name,
        groupRevision: group.revision,
        itemId: item.id,
        position: item.position,
      };
      const existing = rows.get(item.instrument.id);
      if (existing) {
        existing.memberships.push(relation);
        if ((item.observation_revision ?? 0) > existing.observationRevision) {
          existing.favorite = item.favorite ?? false;
          existing.notes = item.notes ?? "";
          existing.observationRevision = item.observation_revision ?? 0;
        }
      } else {
        rows.set(item.instrument.id, {
          instrument: item.instrument,
          memberships: [relation],
          favorite: item.favorite ?? false,
          notes: item.notes ?? "",
          observationRevision: item.observation_revision ?? 0,
        });
      }
    }
  }
  return [...rows.values()];
}

function compareMetric(left?: number | null, right?: number | null, descending = true) {
  const leftValid = typeof left === "number" && Number.isFinite(left);
  const rightValid = typeof right === "number" && Number.isFinite(right);
  if (!leftValid || !rightValid) return leftValid ? -1 : rightValid ? 1 : 0;
  return descending ? right - left : left - right;
}

export function visibleTrackingRows(
  rows: TrackingRow[],
  options: {
    query: string;
    favoritesOnly: boolean;
    sort: TrackingSort;
    metrics: ReadonlyMap<string, TrackingSortMetric>;
  },
) {
  const query = options.query.trim().toLowerCase();
  const visible = rows.filter(
    (row) =>
      (!options.favoritesOnly || row.favorite) &&
      (!query ||
        [row.instrument.symbol, row.instrument.name, row.notes]
          .join(" ")
          .toLowerCase()
          .includes(query)),
  );
  if (options.sort === "manual") return visible;
  return visible.sort((left, right) => {
    if (options.sort === "symbol")
      return left.instrument.symbol.localeCompare(right.instrument.symbol, "en");
    const a = options.metrics.get(left.instrument.id),
      b = options.metrics.get(right.instrument.id);
    if (options.sort === "price_desc") return compareMetric(a?.price, b?.price);
    if (options.sort === "volatility_desc")
      return compareMetric(a?.technical_volatility_level, b?.technical_volatility_level);
    return compareMetric(a?.change_percent, b?.change_percent, options.sort === "change_desc");
  });
}

export function readTrackingView(params: URLSearchParams) {
  const rawSort = params.get("sort");
  const sort: TrackingSort =
    rawSort && Object.hasOwn(trackingSortLabels, rawSort) ? (rawSort as TrackingSort) : "manual";
  return {
    chart: params.get("chart") !== "0",
    favoritesOnly: params.get("favorites") === "1",
    query: params.get("q") ?? "",
    sort,
  };
}

export function trackingSelectionUrl(
  params: URLSearchParams,
  instrument: Pick<InstrumentResponse, "id" | "symbol">,
  showChart?: boolean,
) {
  const next = new URLSearchParams(params);
  next.set("symbol", instrument.symbol);
  if (showChart !== undefined) next.set("chart", showChart ? "1" : "0");
  return `/stocks/${encodeURIComponent(instrument.id)}?${next.toString()}`;
}

export function isCompanyInstrument(instrument: InstrumentResponse | undefined) {
  return instrument?.asset_type === "stock" && !instrument.symbol.startsWith("^");
}
