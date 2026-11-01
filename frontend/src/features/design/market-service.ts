import {
  addWatchlistItem,
  createWatchlist,
  getMarketBars,
  getMarketPulse,
  getTheme,
  getThemeAnalytics,
  getWatchlistTracking,
  listThemes,
  listWatchlists,
  removeWatchlistItem,
  searchInstruments,
  updateWatchlistObservation,
} from "../../shared/api/generated/client";
import type {
  InstrumentResponse,
  WatchlistResponse,
  WatchlistTrackingItem,
} from "../../shared/api/generated/models";
import { collectTrackingRows } from "../watchlists/tracking-utils";

function input(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}
function text(value: unknown) {
  return typeof value === "string" ? value : "";
}
function windowValue(value: unknown): "1D" | "5D" | "1M" {
  return value === "5D" || value === "1M" ? value : "1D";
}
function errorText(error: unknown) {
  return error instanceof Error ? error.message : "行情服务暂时无法读取";
}

/** Each authenticated frame gets its own revision and instrument cache. */
export function createMarketService() {
  let groups: WatchlistResponse[] = [];
  const instruments = new Map<string, InstrumentResponse>();
  const metrics = new Map<string, WatchlistTrackingItem>();

  function remember(instrument: InstrumentResponse) {
    instruments.set(instrument.symbol, instrument);
    return instrument;
  }
  function security(instrument: InstrumentResponse) {
    const metric = metrics.get(instrument.id);
    const memberships = groups.filter((group) =>
      group.items.some((item) => item.instrument.id === instrument.id),
    );
    const observation = collectTrackingRows(groups).find(
      (row) => row.instrument.id === instrument.id,
    );
    return {
      id: instrument.id,
      symbol: instrument.symbol,
      name: instrument.name,
      en: instrument.name,
      type: instrument.asset_type,
      exchange: instrument.exchange,
      currency: instrument.currency,
      price: metric?.price ?? null,
      change: metric?.change_percent ?? null,
      open: null,
      high: null,
      low: null,
      volume: null,
      group: memberships[0]?.name ?? "未分组",
      groups: memberships.map((group) => group.id),
      tile: instrument.asset_type === "etf" ? "blue" : "mint",
      theme:
        metric?.trend === "uptrend"
          ? "向上排列"
          : metric?.trend === "downtrend"
            ? "向下排列"
            : "趋势待核实",
      asOf: metric?.meta.as_of ?? null,
      meta: metric?.meta ?? null,
      observationRevision: observation?.observationRevision ?? 0,
      memberships: observation?.memberships ?? [],
    };
  }
  async function load() {
    groups = await listWatchlists();
    for (const group of groups) for (const item of group.items) remember(item.instrument);
    let warning: string | null = null;
    metrics.clear();
    if (groups.some((group) => group.items.length)) {
      try {
        const tracking = await getWatchlistTracking({ limit: 100 });
        for (const item of tracking.items) metrics.set(item.instrument_id, item);
      } catch (error) {
        metrics.clear();
        warning = errorText(error);
      }
    }
    const rows = collectTrackingRows(groups);
    return {
      securities: [...instruments.values()].map(security),
      watchlist: rows.map((row) => row.instrument.symbol),
      favorites: rows.filter((row) => row.favorite).map((row) => row.instrument.symbol),
      notes: Object.fromEntries(rows.map((row) => [row.instrument.symbol, row.notes])),
      groups: groups.map(({ id, name, revision }) => ({ id, name, revision })),
      warning,
    };
  }
  async function resolve(symbol: string) {
    let instrument = instruments.get(symbol);
    if (!instrument) {
      const response = await searchInstruments({ q: symbol });
      for (const item of response.items) remember(item);
      instrument = instruments.get(symbol);
    }
    if (!instrument) throw new Error("证券目录中找不到此代码，请重新搜索并选择证券。");
    return instrument;
  }
  return async (method: string, payload: unknown): Promise<unknown> => {
    const data = input(payload);
    if (method === "market.load") return load();
    if (method === "market.tracking") {
      const result = await getWatchlistTracking({ limit: 100, refresh: data.refresh === true });
      metrics.clear();
      for (const item of result.items) metrics.set(item.instrument_id, item);
      return result;
    }
    if (method === "market.bars") {
      const period =
        data.period === "1周"
          ? "5d"
          : data.period === "3月"
            ? "3mo"
            : data.period === "1年"
              ? "1y"
              : "1mo";
      return getMarketBars({
        symbol: text(data.symbol),
        period,
        interval: data.interval === "1W" ? "1wk" : "1d",
        adjustment: "provider_native",
        session: "regular",
        limit: 1000,
      });
    }
    if (method === "market.pulse")
      return getMarketPulse({ window: windowValue(data.window), refresh: data.refresh === true });
    if (method === "market.themes") return listThemes();
    if (method === "market.theme") {
      const id = text(data.id);
      const theme = await getTheme(id);
      const analytics = await getThemeAnalytics(id, { window: windowValue(data.window) });
      return { theme, analytics };
    }
    if (method === "market.search") {
      const result = await searchInstruments({ q: text(data.query).slice(0, 80) });
      return { ...result, items: result.items.map((item) => security(remember(item))) };
    }
    if (method === "market.add") {
      const instrument = await resolve(text(data.symbol));
      if (data.groupId && !groups.some((group) => group.id === data.groupId))
        throw new Error("目标自选分组已不存在，请刷新分组后重试。");
      let group = groups.find((item) => item.id === data.groupId) ?? groups[0];
      if (!group) {
        group = await createWatchlist({ name: "我的自选" });
        groups.push(group);
      }
      if (!group.items.some((item) => item.instrument.id === instrument.id)) {
        const updated = await addWatchlistItem(group.id, {
          instrument_id: instrument.id,
          revision: typeof data.revision === "number" ? data.revision : group.revision,
        });
        groups = groups.map((item) => (item.id === updated.id ? updated : item));
      }
      return { ok: true };
    }
    if (method === "market.remove") {
      const instrument = await resolve(text(data.symbol));
      const memberships = groups.filter(
        (group) =>
          (!data.groupId || data.groupId === group.id) &&
          group.items.some((item) => item.instrument.id === instrument.id),
      );
      for (const group of memberships) {
        const item = group.items.find((entry) => entry.instrument.id === instrument.id);
        if (!item) continue;
        const updated = await removeWatchlistItem(group.id, item.id, {
          revision: typeof data.revision === "number" ? data.revision : group.revision,
        });
        groups = groups.map((entry) => (entry.id === updated.id ? updated : entry));
      }
      return { ok: true };
    }
    if (method === "market.favorite" || method === "market.note") {
      const instrument = await resolve(text(data.symbol));
      const row = collectTrackingRows(groups).find((item) => item.instrument.id === instrument.id);
      if (!row) throw new Error("请先将证券加入自选，再保存观察记录。");
      const updated = await updateWatchlistObservation(instrument.id, {
        revision: typeof data.revision === "number" ? data.revision : row.observationRevision,
        ...(method === "market.favorite"
          ? { favorite: data.favorite === true }
          : { notes: text(data.notes) }),
      });
      groups = groups.map((group) => ({
        ...group,
        items: group.items.map((item) =>
          item.instrument.id === instrument.id
            ? {
                ...item,
                favorite: updated.favorite,
                notes: updated.notes,
                observation_revision: updated.revision,
              }
            : item,
        ),
      }));
      return updated;
    }
    return undefined;
  };
}
