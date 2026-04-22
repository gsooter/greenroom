/**
 * Client-side user preferences backed by localStorage.
 *
 * These preferences are intentionally lightweight — they don't round-trip
 * through the backend. For display-only concerns like the distance unit,
 * keeping them in the browser avoids auth coupling and works for
 * signed-out visitors too.
 *
 * Server-rendered pages cannot read localStorage. Consumers that need the
 * preference in SSR should render the default and let the client re-hydrate
 * once the hook reads the stored value.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

export type DistanceUnit = "mi" | "km";

const DISTANCE_UNIT_KEY = "greenroom.pref.distanceUnit";

export const DEFAULT_DISTANCE_UNIT: DistanceUnit = "mi";

function safeReadLocalStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeWriteLocalStorage(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode or quota exceeded — silently ignore */
  }
}

function isDistanceUnit(value: string | null): value is DistanceUnit {
  return value === "mi" || value === "km";
}

/**
 * Read and subscribe to the user's distance unit preference.
 *
 * Returns the stored value (or the default) and a setter that persists
 * the new value. Updates are broadcast via the "storage" event so other
 * tabs and other instances of this hook stay in sync.
 *
 * @returns Tuple of [current unit, setter].
 */
export function useDistanceUnit(): [DistanceUnit, (next: DistanceUnit) => void] {
  const [value, setValue] = useState<DistanceUnit>(DEFAULT_DISTANCE_UNIT);

  useEffect(() => {
    const stored = safeReadLocalStorage(DISTANCE_UNIT_KEY);
    if (isDistanceUnit(stored)) setValue(stored);

    const onStorage = (event: StorageEvent): void => {
      if (event.key !== DISTANCE_UNIT_KEY) return;
      if (isDistanceUnit(event.newValue)) setValue(event.newValue);
    };
    const onCustom = (event: Event): void => {
      const detail = (event as CustomEvent<string | null>).detail;
      if (isDistanceUnit(detail)) setValue(detail);
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("greenroom:distanceUnitChanged", onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("greenroom:distanceUnitChanged", onCustom);
    };
  }, []);

  const update = useCallback((next: DistanceUnit): void => {
    setValue(next);
    safeWriteLocalStorage(DISTANCE_UNIT_KEY, next);
    if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent("greenroom:distanceUnitChanged", { detail: next }),
      );
    }
  }, []);

  return [value, update];
}

const KM_PER_MILE = 1.609344;

/**
 * Convert a kilometer value to the chosen display unit and render it with
 * a short label. Values under one full unit are expressed with one decimal;
 * larger values round to whole units.
 *
 * @param km - Distance in kilometers (from the backend).
 * @param unit - Target display unit.
 * @returns A short, display-ready distance string.
 */
export function formatDistance(km: number, unit: DistanceUnit): string {
  const value = unit === "mi" ? km / KM_PER_MILE : km;
  if (value < 1) {
    return `${value.toFixed(1)} ${unit}`;
  }
  if (value < 10) {
    return `${value.toFixed(1)} ${unit}`;
  }
  return `${Math.round(value)} ${unit}`;
}
