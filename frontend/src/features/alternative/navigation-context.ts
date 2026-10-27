export type AlternativeView = "flows" | "options" | "news";
const symbolPattern = /^[A-Z][A-Z0-9.-]{0,31}$/;

export function validAlternativeSymbol(value: string | null | undefined) {
  const symbol = value?.trim().toUpperCase();
  return symbol &&
    symbolPattern.test(symbol) &&
    (!symbol.includes(".") || /^[A-Z0-9]+\.[AB]$/.test(symbol)) &&
    !/-(?:USD|USDT|USDC|BTC|ETH)$/.test(symbol)
    ? symbol
    : undefined;
}

export function validFlowSymbols(value: string | null | undefined) {
  if (!value) return undefined;
  const symbols = value.split(",").map((item) => validAlternativeSymbol(item));
  return symbols.length <= 10 && symbols.every((item) => !!item)
    ? [...new Set(symbols)].join(",")
    : undefined;
}

export function alternativeViewUrl(view: AlternativeView, params: URLSearchParams) {
  const next = new URLSearchParams();
  const symbol = validAlternativeSymbol(params.get("symbol"));
  const symbols = validFlowSymbols(params.get("symbols"));
  if (symbol) next.set("symbol", symbol);
  if (symbols) next.set("symbols", symbols);
  const hours = params.get("hours"),
    days = params.get("days");
  if (hours && [6, 24, 72, 168].includes(Number(hours))) next.set("hours", hours);
  if (days && [7, 30, 90, 365].includes(Number(days))) next.set("days", days);
  // Account, feed and expiration are view-specific; a stock is never converted into an ETF.
  return `/insights/${view}${next.size ? `?${next}` : ""}`;
}
