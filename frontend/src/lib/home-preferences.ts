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
 *
 * Cold-start default is viewport-aware: a mobile viewport (the
 * Tailwind ``sm`` breakpoint at 640 px and below) defaults to
 * compact because the comfortable layout's vertical hero-image
 * cards stack uncomfortably on a 5-inch screen. Desktop defaults
 * to comfortable. Once the user toggles, the explicit choice is
 * persisted and the viewport default is ignored on subsequent
 * loads — including a desktop user who flips to compact and later
 * resizes, and a mobile user who flips to comfortable and later
 * loads on iPad.
 */

"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "greenroom.home.compact";
const CHANGE_EVENT = "greenroom:home-compact-change";

/** Tailwind's ``sm`` breakpoint — anything narrower is "mobile" here. */
const MOBILE_MAX_WIDTH_PX = 639;

/**
 * Return whether the current viewport is mobile-sized.
 *
 * Used as the cold-start default for compact mode. Returns ``false``
 * outside a browser so SSR renders the desktop layout (which is what
 * crawlers should index regardless of phone-vs-laptop user agents).
 */
function isMobileViewport(): boolean {
  if (typeof window === "undefined") return false;
  if (typeof window.matchMedia === "function") {
    return window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH_PX}px)`).matches;
  }
  return (window.innerWidth ?? Number.POSITIVE_INFINITY) <= MOBILE_MAX_WIDTH_PX;
}

/**
 * Read the current compact-mode preference.
 *
 * Resolution order:
 *
 *   1. Stored ``"true"`` → compact.
 *   2. Stored ``"false"`` → comfortable. (Honors a deliberate opt-out.)
 *   3. No stored value → viewport-aware default (mobile = compact,
 *      desktop = comfortable).
 *
 * Returns ``false`` outside a browser so server components render the
 * desktop layout consistently (crawlers don't get a stripped grid).
 */
export function readCompact(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
    return isMobileViewport();
  } catch {
    return isMobileViewport();
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
