import { beforeEach, describe, expect, it, vi } from "vitest";
import type { InstrumentResponse, WatchlistResponse } from "../../shared/api/generated/models";
import { createMarketService } from "./market-service";

const api = vi.hoisted(() => ({
  addWatchlistItem: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  createWatchlist: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  getMarketBars: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  getMarketPulse: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  getTheme: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  getThemeAnalytics: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  getWatchlistTracking: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  listThemes: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  listWatchlists: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  removeWatchlistItem: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  searchInstruments: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  updateWatchlistObservation: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
}));
vi.mock("../../shared/api/generated/client", () => api);

const instrument: InstrumentResponse = {
  id: "instrument-one",
  symbol: "TEST",
  name: "Test Company",
  asset_type: "stock",
  exchange: "NASDAQ",
  currency: "USD",
  timezone: "America/New_York",
};
function group(id = "group-one", revision = 4): WatchlistResponse {
  return {
    id,
    name: id,
    revision,
    position: 0,
    items: [
      {
        id: `item-${id}`,
        instrument,
        position: 0,
        favorite: false,
        notes: "Original note",
        observation_revision: 7,
      },
    ],
  };
}
const trackingItem = {
  instrument_id: instrument.id,
  price: 123.45,
  change_percent: 1.25,
  trend: "uptrend",
  meta: { source: "fixture-provider", feed: "daily", as_of: "2026-10-30", quality: "complete" },
};

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
  api.listWatchlists.mockResolvedValue([group()]);
  api.getWatchlistTracking.mockResolvedValue({ items: [] });
  api.searchInstruments.mockResolvedValue({
    items: [instrument],
    source: "fixture-directory",
    quality: "complete",
  });
});

describe("design market service", () => {
  it("keeps absent quotes and OHLC values null instead of substituting sample data", async () => {
    const service = createMarketService();
    await expect(service("market.load", {})).resolves.toMatchObject({
      watchlist: ["TEST"],
      favorites: [],
      notes: { TEST: "Original note" },
      groups: [{ id: "group-one", name: "group-one", revision: 4 }],
      securities: [
        {
          symbol: "TEST",
          price: null,
          change: null,
          open: null,
          high: null,
          low: null,
          volume: null,
          asOf: null,
        },
      ],
      warning: null,
    });
  });

  it("replaces earlier tracking coverage when a later response omits a security", async () => {
    api.getWatchlistTracking.mockResolvedValueOnce({ items: [trackingItem] });
    const service = createMarketService();
    await expect(service("market.load", {})).resolves.toMatchObject({
      securities: [{ price: 123.45, change: 1.25, asOf: "2026-10-30" }],
    });
    await expect(service("market.load", {})).resolves.toMatchObject({
      securities: [{ price: null, change: null, asOf: null }],
    });
  });

  it("preserves the real watchlist on tracking failure and clears previous quote values", async () => {
    api.getWatchlistTracking
      .mockResolvedValueOnce({ items: [trackingItem] })
      .mockRejectedValueOnce(new Error("Source unavailable"));
    const service = createMarketService();
    await service("market.load", {});
    await expect(service("market.load", {})).resolves.toMatchObject({
      watchlist: ["TEST"],
      securities: [{ price: null, change: null, meta: null }],
      warning: "Source unavailable",
    });
  });

  it("propagates an obsolete observation revision without retrying or silently replacing it", async () => {
    const conflict = new Error("Revision conflict");
    api.updateWatchlistObservation.mockRejectedValueOnce(conflict);
    const service = createMarketService();
    await service("market.load", {});
    await expect(
      service("market.note", { symbol: "TEST", revision: 2, notes: "Edited elsewhere" }),
    ).rejects.toBe(conflict);
    expect(api.updateWatchlistObservation).toHaveBeenCalledExactlyOnceWith(instrument.id, {
      revision: 2,
      notes: "Edited elsewhere",
    });
    expect(api.listWatchlists).toHaveBeenCalledTimes(1);
  });

  it("uses the successful observation revision for the next write across every membership", async () => {
    api.listWatchlists.mockResolvedValue([group(), group("group-two", 12)]);
    api.updateWatchlistObservation
      .mockResolvedValueOnce({
        instrument_id: instrument.id,
        revision: 8,
        favorite: true,
        notes: "Original note",
      })
      .mockResolvedValueOnce({
        instrument_id: instrument.id,
        revision: 9,
        favorite: true,
        notes: "New note",
      });
    const service = createMarketService();
    await service("market.load", {});
    await service("market.favorite", { symbol: "TEST", favorite: true });
    await service("market.note", { symbol: "TEST", notes: "New note" });
    expect(api.updateWatchlistObservation).toHaveBeenNthCalledWith(1, instrument.id, {
      revision: 7,
      favorite: true,
    });
    expect(api.updateWatchlistObservation).toHaveBeenNthCalledWith(2, instrument.id, {
      revision: 8,
      notes: "New note",
    });
  });

  it("creates a first group using the server revision and adds the selected instrument once", async () => {
    api.listWatchlists.mockResolvedValue([]);
    api.createWatchlist.mockResolvedValue({ ...group("created", 3), items: [] });
    api.addWatchlistItem.mockResolvedValue(group("created", 4));
    const service = createMarketService();
    await service("market.load", {});
    expect(api.getWatchlistTracking).not.toHaveBeenCalled();
    await service("market.add", { symbol: "TEST" });
    await service("market.add", { symbol: "TEST" });
    expect(api.createWatchlist).toHaveBeenCalledExactlyOnceWith({ name: "我的自选" });
    expect(api.addWatchlistItem).toHaveBeenCalledExactlyOnceWith("created", {
      instrument_id: instrument.id,
      revision: 3,
    });
  });

  it("rejects a removed explicit group instead of adding to another group", async () => {
    const service = createMarketService();
    await service("market.load", {});
    await expect(
      service("market.add", { symbol: "TEST", groupId: "deleted-group" }),
    ).rejects.toThrow("分组已不存在");
    expect(api.addWatchlistItem).not.toHaveBeenCalled();
    expect(api.createWatchlist).not.toHaveBeenCalled();
  });

  it("forwards an obsolete add revision and does not retry after the server rejects it", async () => {
    api.listWatchlists.mockResolvedValue([{ ...group(), items: [] }]);
    api.addWatchlistItem.mockRejectedValue(new Error("Revision conflict"));
    const service = createMarketService();
    await service("market.load", {});
    await expect(
      service("market.add", { symbol: "TEST", groupId: "group-one", revision: 1 }),
    ).rejects.toThrow("Revision conflict");
    expect(api.addWatchlistItem).toHaveBeenCalledExactlyOnceWith("group-one", {
      instrument_id: instrument.id,
      revision: 1,
    });
  });

  it("removes the correct item from each group with that group's revision", async () => {
    api.listWatchlists.mockResolvedValue([group(), group("group-two", 12)]);
    api.removeWatchlistItem.mockImplementation((...args: unknown[]) =>
      Promise.resolve({ ...group(String(args[0])), items: [] }),
    );
    const service = createMarketService();
    await service("market.load", {});
    await service("market.remove", { symbol: "TEST" });
    expect(api.removeWatchlistItem).toHaveBeenNthCalledWith(1, "group-one", "item-group-one", {
      revision: 4,
    });
    expect(api.removeWatchlistItem).toHaveBeenNthCalledWith(2, "group-two", "item-group-two", {
      revision: 12,
    });
  });

  it("limits removal to an explicitly selected group and forwards obsolete group revisions", async () => {
    api.listWatchlists.mockResolvedValue([group(), group("group-two", 12)]);
    api.removeWatchlistItem.mockRejectedValue(new Error("Revision conflict"));
    const service = createMarketService();
    await service("market.load", {});
    await expect(
      service("market.remove", { symbol: "TEST", groupId: "group-two", revision: 1 }),
    ).rejects.toThrow("Revision conflict");
    expect(api.removeWatchlistItem).toHaveBeenCalledExactlyOnceWith("group-two", "item-group-two", {
      revision: 1,
    });
  });

  it.each([
    ["1周", "1D", "5d", "1d"],
    ["1月", "1W", "1mo", "1wk"],
    ["3月", "1D", "3mo", "1d"],
    ["1年", "1W", "1y", "1wk"],
  ])(
    "requests %s / %s with an explicit real adjustment and session",
    async (period, interval, expectedPeriod, expectedInterval) => {
      const response = { bars: [], meta: { quality: "unavailable" } };
      api.getMarketBars.mockResolvedValue(response);
      const service = createMarketService();
      await expect(service("market.bars", { symbol: "TEST", period, interval })).resolves.toBe(
        response,
      );
      expect(api.getMarketBars).toHaveBeenCalledExactlyOnceWith({
        symbol: "TEST",
        period: expectedPeriod,
        interval: expectedInterval,
        adjustment: "provider_native",
        session: "regular",
        limit: 1000,
      });
    },
  );

  it("forwards the selected pulse and theme windows without multiplying sample returns", async () => {
    api.getTheme.mockResolvedValue({ id: "theme-one" });
    api.getThemeAnalytics.mockResolvedValue({ members: [] });
    const service = createMarketService();
    await service("market.pulse", { window: "5D", refresh: true });
    await service("market.theme", { id: "theme-one", window: "1M" });
    expect(api.getMarketPulse).toHaveBeenCalledExactlyOnceWith({ window: "5D", refresh: true });
    expect(api.getTheme).toHaveBeenCalledExactlyOnceWith("theme-one");
    expect(api.getThemeAnalytics).toHaveBeenCalledExactlyOnceWith("theme-one", { window: "1M" });
  });

  it("leaves unrelated RPC methods for other services", async () => {
    await expect(createMarketService()("investment.load", {})).resolves.toBeUndefined();
    expect(Object.values(api).every((mock) => mock.mock.calls.length === 0)).toBe(true);
  });
});
