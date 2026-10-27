export function normalizedUsername(value: string) {
  return value.trim().toLowerCase();
}

export function validUsername(value: string) {
  return /^[a-z0-9][a-z0-9._-]{2,31}$/.test(normalizedUsername(value));
}

export function validNewPassword(value: string) {
  // Match the backend's Unicode code point length, including supplementary characters.
  const length = Array.from(value).length;
  return length >= 12 && length <= 128 && value.trim().length > 0;
}

/** Only workbench-relative destinations can survive an authentication redirect. */
export function safeReturnPath(value: string | null | undefined): string {
  const fallback = "/research/new";
  if (
    !value?.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    Array.from(value).some(
      (character) => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127,
    )
  )
    return fallback;
  try {
    const parsed = new URL(value, "https://workbench.invalid");
    if (
      parsed.origin !== "https://workbench.invalid" ||
      !/^\/(?:research|runs|stocks|reports|themes|insights|settings|market|sectors|account)(?:\/|$)/.test(
        parsed.pathname,
      )
    )
      return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function authPageUrl(page: "login" | "register" | "setup", next?: string | null) {
  const destination = safeReturnPath(next);
  return `/${page}${destination === "/research/new" ? "" : `?${new URLSearchParams({ next: destination })}`}`;
}
