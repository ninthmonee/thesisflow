import { describe, expect, it } from "vitest";
import {
  authPageUrl,
  normalizedUsername,
  safeReturnPath,
  validNewPassword,
  validUsername,
} from "./auth-forms";

describe("authentication form boundaries", () => {
  it("uses the same bounded normalized username as the account service", () => {
    expect(normalizedUsername("  Research.One ")).toBe("research.one");
    expect(validUsername("Research.One")).toBe(true);
    for (const value of ["ab", "_one", "研究员", "a b", "a".repeat(33)])
      expect(validUsername(value)).toBe(false);
  });

  it("allows long Unicode/spaced passwords without forced composition", () => {
    expect(validNewPassword("words words words")).toBe(true);
    expect(validNewPassword("中".repeat(12))).toBe(true);
    expect(validNewPassword("🚀".repeat(128))).toBe(true);
    expect(validNewPassword("x".repeat(128))).toBe(true);
    expect(validNewPassword("x".repeat(129))).toBe(false);
    expect(validNewPassword("short")).toBe(false);
    expect(validNewPassword(" ".repeat(15))).toBe(false);
  });

  it("keeps intended workbench views without accepting external or auth redirects", () => {
    expect(safeReturnPath("/stocks?symbol=NVDA")).toBe("/stocks?symbol=NVDA");
    expect(safeReturnPath("/runs/record#reports")).toBe("/runs/record#reports");
    expect(safeReturnPath("/account")).toBe("/account");
    for (const value of [
      "https://example.com",
      "//example.com",
      "/\\example.com",
      "/login",
      "/register?next=/stocks",
      "/stocks/../login",
      "/stocks\n",
      "/api/v1/models",
      "javascript:alert(1)",
      null,
    ])
      expect(safeReturnPath(value)).toBe("/research/new");
  });

  it("encodes only a validated local destination while switching auth forms", () => {
    expect(authPageUrl("register", "/reports?ticker=NVDA")).toBe(
      "/register?next=%2Freports%3Fticker%3DNVDA",
    );
    expect(authPageUrl("login", "https://example.com")).toBe("/login");
    expect(authPageUrl("setup")).toBe("/setup");
  });
});
