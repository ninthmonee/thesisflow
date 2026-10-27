import type {
  ResearchDataConnectionCreate,
  ResearchDataConnectionView,
} from "../../shared/api/generated/models";

export type ResearchConnectionFields = Omit<
  ResearchDataConnectionCreate,
  "api_key" | "user_agent"
> & {
  api_key?: string;
  user_agent?: string;
};

/** Select only credentials for the chosen provider and explicitly selected source. */
export function researchConnectionPayload(
  values: ResearchConnectionFields,
  provider: ResearchDataConnectionCreate["provider"],
): Omit<ResearchDataConnectionCreate, "provider"> {
  const source = values.credential_source ?? "keychain";
  return {
    name: values.name.trim(),
    credential_source: source,
    enabled: values.enabled ?? true,
    ...(source === "keychain" && provider === "typesafe_jev"
      ? { model: values.model?.trim(), base_url: values.base_url }
      : {}),
    ...(source === "keychain" && provider !== "sec_edgar" && values.api_key?.trim()
      ? { api_key: values.api_key.trim() }
      : {}),
    ...(source === "keychain" && provider === "sec_edgar" && values.user_agent?.trim()
      ? { user_agent: values.user_agent.trim() }
      : {}),
  };
}

export function hasSavedResearchCredential(
  connection: ResearchDataConnectionView | undefined,
  source: ResearchDataConnectionCreate["credential_source"],
  provider: ResearchDataConnectionCreate["provider"],
) {
  return (
    source === "keychain" &&
    connection?.credential_source === "keychain" &&
    (provider === "sec_edgar" ? connection.user_agent_configured : connection.credential_configured)
  );
}
