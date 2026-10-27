import type { InstrumentResponse, WatchlistResponse } from "../../shared/api/generated/models";

export type EquitySelection =
  | { state: "ready"; symbol: string; instrument?: InstrumentResponse }
  | { state: "loading" | "missing" | "error" };

export function resolveEquitySelection(input: {
  instrumentId?: string;
  explicitSymbol?: string | null;
  groups?: WatchlistResponse[];
  selectedGroupId?: string;
  status: "pending" | "success" | "error";
}): EquitySelection {
  const explicitSymbol = input.explicitSymbol?.trim().toUpperCase();
  if (explicitSymbol && /^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$/.test(explicitSymbol)) {
    const instrument = input.groups
      ?.flatMap((group) => group.items)
      .find((item) => item.instrument.symbol.trim().toUpperCase() === explicitSymbol)?.instrument;
    return { state: "ready", symbol: explicitSymbol, ...(instrument ? { instrument } : {}) };
  }
  if (input.groups === undefined) {
    if (input.status === "pending") return { state: "loading" };
    return { state: input.status === "error" ? "error" : "missing" };
  }
  if (input.instrumentId) {
    const instrument = input.groups
      .flatMap((group) => group.items)
      .find((item) => item.instrument.id === input.instrumentId)?.instrument;
    return instrument
      ? { state: "ready", symbol: instrument.symbol, instrument }
      : { state: "missing" };
  }
  const selected =
    input.groups.find((group) => group.id === input.selectedGroupId) ?? input.groups[0];
  const instrument = selected?.items[0]?.instrument;
  return instrument
    ? { state: "ready", symbol: instrument.symbol, instrument }
    : { state: "ready", symbol: "SPY" };
}
