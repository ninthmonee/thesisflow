import { describe, expect, it } from "vitest";
import { getListResearchRunsUrl } from "../../shared/api/generated/client";
import { historyParameters } from "./history-query";

describe("research history filters", () => {
  it("omits cleared filters from the actual generated request URL", () => {
    const url = new URL(getListResearchRunsUrl(historyParameters("  ")), "http://localhost");
    expect([...url.searchParams]).toEqual([["limit", "20"]]);
    expect(url.searchParams.has("ticker")).toBe(false);
    expect(url.searchParams.has("cursor")).toBe(false);
    expect(url.searchParams.has("status")).toBe(false);
  });

  it("preserves explicit filters and pagination without serializing null", () => {
    const url = new URL(
      getListResearchRunsUrl(historyParameters(" aapl ", "completed", "next:20")),
      "http://localhost",
    );
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: "20",
      ticker: "AAPL",
      status: "completed",
      cursor: "next:20",
    });
  });
});
