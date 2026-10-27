import type {
  ListResearchRunsParams,
  ListResearchRunsStatus,
} from "../../shared/api/generated/models";

export function historyParameters(
  ticker: string,
  status?: ListResearchRunsStatus,
  cursor?: string,
): ListResearchRunsParams {
  const symbol = ticker.trim().toUpperCase();
  return {
    limit: 20,
    ...(symbol ? { ticker: symbol } : {}),
    ...(status ? { status } : {}),
    ...(cursor ? { cursor } : {}),
  };
}
