/**
 * Genre → pin color mapping for the Tonight map surface.
 *
 * The Tonight map colors each pin by the event's headliner genre so a
 * user can skim the whole city at a glance. Colors are CSS variable
 * references that resolve in both component styles and inline MapKit
 * annotation colors.
 *
 * Keep this list short and opinionated — adding one color per sub-
 * genre would defeat the purpose. New genres fall through to
 * `DEFAULT_MAP_COLOR`.
 */

import type { CSSProperties } from "react";

export type MapPinColor =
  | "green"
  | "blush"
  | "navy"
  | "amber"
  | "coral"
  | "gold";

const GROUP_INDIE_ROCK: readonly string[] = [
  "indie",
  "indie rock",
  "rock",
  "alternative",
  "punk",
  "post-punk",
  "emo",
];

const GROUP_POP_FOLK: readonly string[] = [
  "pop",
  "folk",
  "singer-songwriter",
  "americana",
  "country",
];

const GROUP_ELECTRONIC: readonly string[] = [
  "electronic",
  "dance",
  "house",
  "techno",
  "edm",
  "dj",
];

const GROUP_HIP_HOP: readonly string[] = [
  "hip-hop",
  "hip hop",
  "rap",
  "trap",
];

const GROUP_JAZZ_SOUL: readonly string[] = [
  "jazz",
  "soul",
  "r&b",
  "rnb",
  "funk",
  "blues",
];

/**
 * Ordered table of (genre group → pin color) pairs.
 *
 * Earlier entries win; an event tagged both "indie" and "electronic"
 * renders as indie.
 */
const GENRE_GROUPS: ReadonlyArray<{
  color: MapPinColor;
  genres: readonly string[];
}> = [
  { color: "green", genres: GROUP_INDIE_ROCK },
  { color: "blush", genres: GROUP_POP_FOLK },
  { color: "amber", genres: GROUP_ELECTRONIC },
  { color: "coral", genres: GROUP_HIP_HOP },
  { color: "gold", genres: GROUP_JAZZ_SOUL },
];

export const DEFAULT_MAP_COLOR: MapPinColor = "navy";

const CSS_VAR_BY_COLOR: Readonly<Record<MapPinColor, string>> = {
  green: "var(--color-green-primary)",
  blush: "var(--color-blush-accent)",
  navy: "var(--color-navy-dark)",
  amber: "var(--color-amber)",
  coral: "var(--color-coral)",
  gold: "var(--color-gold)",
};

/**
 * Resolve an event's genre list to a single pin color bucket.
 *
 * Comparison is case-insensitive. The first matching genre wins; if no
 * genre matches any group, returns {@link DEFAULT_MAP_COLOR} so the pin
 * still renders.
 *
 * @param genres - The event's `genres` array from the API.
 * @returns A {@link MapPinColor} bucket name.
 */
export function pinColorForGenres(
  genres: readonly string[] | null | undefined,
): MapPinColor {
  if (!genres || genres.length === 0) return DEFAULT_MAP_COLOR;
  const normalized = genres.map((g) => g.toLowerCase().trim());
  for (const group of GENRE_GROUPS) {
    if (normalized.some((g) => group.genres.includes(g))) {
      return group.color;
    }
  }
  return DEFAULT_MAP_COLOR;
}

/**
 * Inline style carrying the pin-bucket's CSS variable as `--pin-color`.
 *
 * Components render the pin dot with `backgroundColor: var(--pin-color)`
 * so the palette stays single-sourced in `globals.css`.
 *
 * @param color - The pin color bucket name.
 * @returns A React `CSSProperties` object suitable for `style={...}`.
 */
export function pinColorStyle(color: MapPinColor): CSSProperties {
  return { "--pin-color": CSS_VAR_BY_COLOR[color] } as CSSProperties;
}

/**
 * Exposed for components that need the raw CSS variable reference
 * (for example, to set a MapKit annotation's `color` property that
 * can't read `--pin-color` off a DOM ancestor).
 *
 * @param color - The pin color bucket name.
 * @returns The `var(--color-*)` reference string.
 */
export function pinColorVariable(color: MapPinColor): string {
  return CSS_VAR_BY_COLOR[color];
}
