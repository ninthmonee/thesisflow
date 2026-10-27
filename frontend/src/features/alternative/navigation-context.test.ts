import { describe, expect, it } from "vitest";
import { alternativeViewUrl, validAlternativeSymbol, validFlowSymbols } from "./navigation-context";

describe("cross-view public security context", () => {
  it("preserves the selected security and valid windows without forwarding accounts or expiry", () => {
    const url = alternativeViewUrl(
      "news",
      new URLSearchParams(
        "symbol=AMD&hours=72&days=30&provider=alpaca&connection=private&expiration=2026-12-01&api_key=secret",
      ),
    );
    expect(url).toBe("/insights/news?symbol=AMD&hours=72&days=30");
  });
  it("does not reinterpret a stock as an ETF or silently choose a sample ticker", () => {
    const url = alternativeViewUrl("flows", new URLSearchParams("symbol=AMD"));
    expect(url).toBe("/insights/flows?symbol=AMD");
    expect(new URLSearchParams(url.split("?")[1]).has("symbols")).toBe(false);
    expect(validAlternativeSymbol(null)).toBeUndefined();
    expect(validFlowSymbols(null)).toBeUndefined();
  });
  it("retains a separately selected ETF basket across other views", () => {
    expect(
      alternativeViewUrl("options", new URLSearchParams("symbol=MU&symbols=SOXX,IVV&hours=24")),
    ).toBe("/insights/options?symbol=MU&symbols=SOXX%2CIVV&hours=24");
    expect(validFlowSymbols("soxx, IVV,SOXX")).toBe("SOXX,IVV");
  });
  it("rejects unsupported formats, crypto pairs and invalid time choices", () => {
    for (const symbol of [
      "^GSPC",
      "EURUSD=X",
      "BTC-USD",
      "ETH-USDT",
      "bad/code",
      "9988.HK",
      "SHOP.TO",
    ])
      expect(validAlternativeSymbol(symbol)).toBeUndefined();
    expect(validFlowSymbols("SOXX,")).toBeUndefined();
    expect(validFlowSymbols(Array.from({ length: 11 }, () => "SOXX").join(","))).toBeUndefined();
    expect(validAlternativeSymbol("brk.b")).toBe("BRK.B");
    expect(alternativeViewUrl("news", new URLSearchParams("hours=12&days=-1"))).toBe(
      "/insights/news",
    );
  });
});
