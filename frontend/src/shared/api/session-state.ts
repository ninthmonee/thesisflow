/** Memory-only request ownership. The browser owns the HttpOnly session cookie. */
interface SessionIdentity {
  userId: string;
  workspaceId: string;
  csrfToken: string;
}

let identity: SessionIdentity | undefined;
let generation = 0;
let expired = false;
const expirationListeners = new Set<() => void>();

export function authGeneration() {
  return generation;
}

export function installAuthSession(next: SessionIdentity) {
  if (
    !expired &&
    identity?.userId === next.userId &&
    identity.workspaceId === next.workspaceId &&
    identity.csrfToken === next.csrfToken
  )
    return;
  identity = { ...next };
  generation += 1;
  expired = false;
}

export function clearAuthSession() {
  if (identity) generation += 1;
  identity = undefined;
  expired = true;
}

export function csrfToken() {
  return identity?.csrfToken;
}

export function authWorkspaceId() {
  return identity?.workspaceId;
}

export function subscribeSessionExpired(listener: () => void) {
  expirationListeners.add(listener);
  return () => {
    expirationListeners.delete(listener);
  };
}

export function notifySessionExpired(requestGeneration = generation) {
  if (requestGeneration !== generation || expired) return;
  expired = true;
  identity = undefined;
  generation += 1;
  for (const listener of expirationListeners) listener();
}

export function assertCurrentSession(requestGeneration: number) {
  if (requestGeneration !== generation)
    throw new DOMException("Request belongs to an earlier session", "AbortError");
}
