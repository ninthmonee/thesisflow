import { describe, expect, it } from "vitest";
import type { ResearchDataConnectionView } from "../../shared/api/generated/models";
import {
  hasSavedResearchCredential,
  researchConnectionPayload,
} from "./research-connection-fields";

describe("research connection credential boundaries", () => {
  it("does not send hidden credentials or Jev parameters when using the explicit environment source", () => {
    const result = researchConnectionPayload(
      {
        provider: "typesafe_jev",
        name: "Jev",
        credential_source: "legacy_environment",
        api_key: "private-test-key",
        user_agent: "private-test-contact",
        model: "custom",
        base_url: "https://api.typesafe.ai",
      },
      "typesafe_jev",
    );
    expect(result).toEqual({ name: "Jev", enabled: true, credential_source: "legacy_environment" });
  });
  it("sends only the credential family belonging to the selected provider", () => {
    const fields = {
      provider: "fred" as const,
      name: " FRED ",
      api_key: " test-only ",
      user_agent: "must-not-send",
      model: "unrelated",
    };
    expect(researchConnectionPayload(fields, "fred")).toEqual({
      name: "FRED",
      enabled: true,
      credential_source: "keychain",
      api_key: "test-only",
    });
    expect(researchConnectionPayload(fields, "sec_edgar")).toEqual({
      name: "FRED",
      enabled: true,
      credential_source: "keychain",
      user_agent: "must-not-send",
    });
  });
  it("omits blank secrets so editing metadata preserves the existing credential", () => {
    expect(
      researchConnectionPayload(
        { provider: "fred", name: "FRED", api_key: "  ", enabled: false },
        "fred",
      ),
    ).toEqual({ name: "FRED", enabled: false, credential_source: "keychain" });
  });
  it("requires a new Keychain credential when switching away from the environment source", () => {
    const connection = {
      credential_source: "legacy_environment",
      credential_configured: true,
    } as ResearchDataConnectionView;
    expect(hasSavedResearchCredential(connection, "keychain", "fred")).toBe(false);
    expect(
      hasSavedResearchCredential(
        { ...connection, credential_source: "keychain" },
        "keychain",
        "fred",
      ),
    ).toBe(true);
  });
  it("treats the SEC contact identity separately from an API key", () => {
    const connection = {
      credential_source: "keychain",
      credential_configured: false,
      user_agent_configured: true,
    } as ResearchDataConnectionView;
    expect(hasSavedResearchCredential(connection, "keychain", "sec_edgar")).toBe(true);
    expect(hasSavedResearchCredential(connection, "keychain", "fred")).toBe(false);
  });
});
