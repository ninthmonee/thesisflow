import { describe, expect, it } from "vitest";
import type { ETFFlowSeries, OptionContract } from "../../shared/api/generated/models";
import { externalArticle, flowSummary, optionCurve } from "./alternative-utils";

const contract = (
  symbol: string,
  type: "call" | "put",
  strike: number,
  iv: number | null,
  oi: number | null,
): OptionContract => ({
  symbol,
  contract_type: type,
  expiration: "2026-10-30",
  strike,
  implied_volatility: iv,
  open_interest: oi,
});

describe("alternative observation transformations", () => {
  it("reports known-value coverage instead of turning missing daily flows into zero", () => {
    const fund: ETFFlowSeries = {
      symbol: "SOXX",
      points: [
        {
          date: "2026-10-23",
          as_of_date: null,
          nav: 100,
          shares_outstanding: 10,
          net_flow_usd: null,
          flow_kind: "estimated",
          quality: "action_required",
          method: "guard",
        },
        {
          date: "2026-10-22",
          as_of_date: "2026-10-21",
          nav: 100,
          shares_outstanding: 10,
          net_flow_usd: -100,
          flow_kind: "estimated",
          quality: "complete",
          method: "formula",
        },
      ],
      meta: {
        source: "fixture",
        feed: "fixture",
        as_of: null,
        received_at: "2026-10-25T00:00:00Z",
        quality: "partial",
        delay_label: "daily",
        scope: "fixture",
      },
    };
    expect(flowSummary(fund)).toMatchObject({
      knownSum: -100,
      validCount: 1,
      missingCount: 1,
      latest: { date: "2026-10-23" },
    });
    expect(
      flowSummary({ ...fund, points: [{ ...fund.points[0]!, net_flow_usd: null }] }).knownSum,
    ).toBeNull();
    expect(fund.points[0]?.date).toBe("2026-10-23");
  });

  it("sorts strikes while preserving zero, missing IV and unmatched contract sides", () => {
    const result = optionCurve(
      [
        contract("C2", "call", 200, 0.3, 50),
        contract("P1", "put", 100, null, 0),
        contract("C1", "call", 100, 0, null),
      ],
      "implied_volatility",
    );
    expect(result).toEqual({ strikes: [100, 200], calls: [0, 0.3], puts: [null, null] });
    expect(optionCurve([contract("P1", "put", 100, null, 0)], "open_interest").puts).toEqual([0]);
  });

  it("does not aggregate adjusted option contracts sharing a strike", () => {
    const contracts = [
      contract("C-standard", "call", 100, 0.3, 20),
      contract("C-adjusted", "call", 100, 0.6, 50),
    ];
    expect(optionCurve(contracts, "open_interest").calls).toEqual([null]);
    expect(contracts).toHaveLength(2);
  });

  it("allows only public web article links without embedded credentials", () => {
    expect(externalArticle("https://publisher.example/a")).toBe("https://publisher.example/a");
    for (const value of [
      "javascript:alert(1)",
      "file:///etc/passwd",
      "https://user:key@publisher.example/a",
      "/relative",
      null,
    ])
      expect(externalArticle(value)).toBeUndefined();
  });
});
