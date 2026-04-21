/**
 * Stable per-browser guest session id.
 *
 * Used only by venue comment votes so signed-out visitors don't stack
 * multiple up-votes on the same comment. It's NOT an auth credential —
 * it just deduplicates guest votes at the server. Backend accepts a
 * session_id up to 64 chars; a UUID easily fits.
 *
 * The id is persisted in localStorage under `greenroom.guest_session`.
 * If storage is disabled (Safari private mode, etc.) we fall back to
 * an in-memory id that lives for the page lifetime.
 */

const STORAGE_KEY = "greenroom.guest_session";

let memoryFallback: string | null = null;

function generateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Back-compat fallback for older browsers: timestamp + random suffix.
  return `g-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Return (creating if necessary) the current guest session id.
 * Safe to call from any client component.
 */
export function getGuestSessionId(): string {
  if (typeof window === "undefined") {
    // SSR never needs a guest id — callers should skip the vote button
    // until after hydration. Returning a deterministic string keeps
    // any accidental server-side usage from crashing.
    return "ssr-placeholder";
  }
  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing) return existing;
    const fresh = generateId();
    window.localStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    if (memoryFallback) return memoryFallback;
    memoryFallback = generateId();
    return memoryFallback;
  }
}
