import { describe, expect, it } from "vitest";
import { getGetProfessionalMarketPulseUrl } from "../../shared/api/generated/client";
import { professionalPulseParameters } from "./professional-pulse-params";
import type { ProfessionalPulseSelection } from "./professional-pulse-params";

const empty: ProfessionalPulseSelection = {
  options: "",
  etfs: "",
  news: "",
  includeNews: false,
  optionsProvider: "yahoo",
  flowsProvider: "ishares",
  lookbackDays: 30,
  lookbackHours: 24,
};

describe("explicit professional sample requests", () => {
  it("does not silently choose NVDA, an ETF or broad news and omits empty optional queries", () => {
    const url = new URL(
      getGetProfessionalMarketPulseUrl(professionalPulseParameters(empty)),
      "http://localhost",
    );
    expect(url.searchParams.has("options_symbols")).toBe(false);
    expect(url.searchParams.has("etf_symbols")).toBe(false);
    expect(url.searchParams.has("news_symbol")).toBe(false);
    expect(url.searchParams.get("include_news")).toBe("false");
    expect(url.href).not.toMatch(/null|undefined|NVDA/);
  });

  it("requires explicit opt-in for broad publisher news samples", () => {
    const result = professionalPulseParameters({ ...empty, includeNews: true });
    expect(result.include_news).toBe(true);
    expect(result.news_symbol).toBeUndefined();
    expect(
      professionalPulseParameters({ ...empty, news: "NVDA", newsConnection: "saved-account" })
        .news_connection_id,
    ).toBeUndefined();
  });

  it("preserves individual scopes and only includes accounts for selected providers", () => {
    expect(
      professionalPulseParameters({
        ...empty,
        options: " nvda,AMD,NVDA ",
        etfs: "SOXX",
        optionsProvider: "massive",
        optionsConnection: "options-account",
        flowsConnection: "ignored-free-account",
      }),
    ).toMatchObject({
      options_symbols: "NVDA,AMD",
      etf_symbols: "SOXX",
      options_connection_id: "options-account",
    });
    expect(
      professionalPulseParameters({ ...empty, options: "NVDA", optionsConnection: "ignored" })
        .options_connection_id,
    ).toBeUndefined();
  });

  it.each(["EURUSD=X", "ES=F", "NVDA.L", "BTC-USD", "NVDA,", "A,B,C,D,E,F"])(
    "rejects unsupported or unbounded options selection %s",
    (options) => {
      expect(() => professionalPulseParameters({ ...empty, options })).toThrow(/有效美股代码/);
    },
  );

  it("accepts American share classes and ADR tickers", () => {
    expect(
      professionalPulseParameters({ ...empty, options: "BRK.B,BRK-B,TSM,BNTX" }).options_symbols,
    ).toBe("BRK.B,BRK-B,TSM,BNTX");
  });
});
