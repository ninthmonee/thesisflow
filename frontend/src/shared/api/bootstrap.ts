import { useQuery } from "@tanstack/react-query";
import { getBootstrap } from "./generated/client";
import { GetBootstrapResponse } from "./generated/validators";
import { queryKeys } from "./query-keys";
import type { QueryScope } from "./query-keys";
import { shouldRetryQuery } from "./transport";

// Local-only identity until an authenticated session/workspace selector is introduced.
const localScope: QueryScope = { userId: "local-session", workspaceId: "local-session" };

export function useBootstrap(scope: QueryScope = localScope) {
  return useQuery({
    queryKey: queryKeys.bootstrap(scope),
    queryFn: async ({ signal }) => GetBootstrapResponse.parse(await getBootstrap({ signal })),
    staleTime: 15_000,
    retry: shouldRetryQuery,
    refetchOnWindowFocus: true,
  });
}
