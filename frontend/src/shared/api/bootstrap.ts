import { useQuery } from "@tanstack/react-query";
import { getBootstrap } from "./generated/client";
import { GetBootstrapResponse } from "./generated/validators";
import { queryKeys } from "./query-keys";
import type { QueryScope } from "./query-keys";
import { shouldRetryQuery } from "./transport";

export function useBootstrap(scope: QueryScope) {
  return useQuery({
    queryKey: queryKeys.bootstrap(scope),
    queryFn: async ({ signal }) => GetBootstrapResponse.parse(await getBootstrap({ signal })),
    staleTime: 15_000,
    retry: shouldRetryQuery,
    refetchOnWindowFocus: true,
  });
}
