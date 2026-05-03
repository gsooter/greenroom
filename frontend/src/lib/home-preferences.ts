/**
 * Client-side preference for the home page card density.
 *
 * Persists in ``localStorage`` so the toggle survives reloads. A
 * lightweight ``CustomEvent`` keeps every consumer in sync within
 * the same tab — both the personalized sections and the SSR-hydrated
 * browse grid read the same key, and one toggle re-renders all of
 * them without prop-drilling through unrelated components.
 *
 * SSR-safe: every helper guards on ``typeof window`` so server
 * components can import this module without a window reference at
 * render time. The hook always reports ``false`` until after mount,
 * which is the correct value for both anonymous visitors (who
 * haven't set the preference) and crawlers (who shouldn't see a
 * stripped layout).
 */

"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "greenroom.home.compact";
const CHANGE_EVENT = "greenroom:home-compact-change";

/**
 * Read the current compact-mode preference from ``localStorage``.
 *
 * Returns ``false`` outside of a browser, when the key is missing,
 * or when the stored value is anything other than the literal
 * ``"true"`` — defensive against legacy values left over from
 * earlier iterations.
 */
export function readCompact(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/**
 * Persist the compact-mode preference and notify same-tab listeners.
 */
export function writeCompact(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "true" : "false");
  } catch {
    /* storage may be disabled; fail quietly */
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/**
 * React hook returning the current compact-mode preference and a
 * setter that persists.
 *
 * Rehydrates from storage on mount and keeps multiple consumers in
 * the same tab in sync via the custom change event.
 */
export function useCompactMode(): [boolean, (next: boolean) => void] {
  const [compact, setCompact] = useState<boolean>(false);

  useEffect(() => {
    setCompact(readCompact());
    const onChange = (): void => setCompact(readCompact());
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);

  const update = (next: boolean): void => {
    writeCompact(next);
    setCompact(next);
  };

  return [compact, update];
}
