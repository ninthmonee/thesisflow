import { describe, expect, it } from "vitest";
import { queryKeys } from "./query-keys";

describe("query ownership", () => {
  it("separates both workspaces and users", () => {
    const first = { userId: "one", workspaceId: "a" };
    expect(queryKeys.bootstrap(first)).not.toEqual(
      queryKeys.bootstrap({ ...first, workspaceId: "b" }),
    );
    expect(queryKeys.bootstrap(first)).not.toEqual(
      queryKeys.bootstrap({ ...first, userId: "two" }),
    );
  });

  it("keeps feed and adjustment in resource identity", () => {
    const scope = { userId: "one", workspaceId: "a" };
    expect(queryKeys.resource(scope, "bars", { feed: "iex", adjustment: "raw" })).not.toEqual(
      queryKeys.resource(scope, "bars", { feed: "sip", adjustment: "split" }),
    );
  });
});
