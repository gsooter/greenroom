/**
 * Pure helpers for the /events filter panel.
 *
 * Filters live in the URL so they're shareable, SSR-safe, and back/forward
 * navigable. This module owns the bidirectional translation between the
 * raw search-param record (which the page receives from Next) and the
 * structured ``EventFilters`` shape the UI hands around. No React here.
 */

export interface EventFilters {
  /** Genre slugs the user wants to keep (OR'd against event genres). */
  genres: string[];
  /** Venue UUIDs to restrict to. */
  venueIds: string[];
  /** Free-text artist substring. ``null`` when unset. */
  artistSearch: string | null;
  /** Inclusive upper bound on min_price in dollars. ``null`` when unset. */
  priceMax: number | null;
  /** True when the user only wants free shows. Overrides ``priceMax``. */
  freeOnly: boolean;
  /** True drops cancelled, sold-out, and past shows from the result. */
  availableOnly: boolean;
  /** ``YYYY-MM-DD`` lower bound, or null for "default to today". */
  dateFrom: string | null;
  /** ``YYYY-MM-DD`` upper bound, or null for no upper bound. */
  dateTo: string | null;
}

/** Filter keys this module owns in the URL. */
export const FILTER_PARAM_KEYS = [
  "genre",
  "venue",
  "artist",
  "price_max",
  "free",
  "available",
  "date_from",
  "date_to",
] as const;

export const EMPTY_FILTERS: EventFilters = {
  genres: [],
  venueIds: [],
  artistSearch: null,
  priceMax: null,
  freeOnly: false,
  availableOnly: false,
  dateFrom: null,
  dateTo: null,
};

/** Source for ``parseEventFilters`` — accepts both Next's record shape and ``URLSearchParams``. */
type ParamsLike =
  | URLSearchParams
  | Record<string, string | string[] | undefined>;

function readParam(
  source: ParamsLike,
  key: string,
): string | null {
  if (source instanceof URLSearchParams) {
    return source.get(key);
  }
  const value = source[key];
  if (value === undefined) return null;
  return Array.isArray(value) ? (value[0] ?? null) : value;
}

function parseDateParam(value: string | null): string | null {
  if (!value) return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function parseBoolParam(value: string | null): boolean {
  if (value === null) return false;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function splitCsv(value: string | null): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

/**
 * Decode a URL-style param record into a fully populated EventFilters.
 *
 * Unknown / malformed values are silently dropped — matching the
 * server-side parser's "be lenient on input" stance — so a stale URL
 * never produces a 4xx, just an empty filter on the offending field.
 */
export function parseEventFilters(source: ParamsLike): EventFilters {
  const priceMaxRaw = readParam(source, "price_max");
  const priceMaxNum = priceMaxRaw === null ? NaN : Number.parseFloat(priceMaxRaw);
  return {
    genres: splitCsv(readParam(source, "genre")),
    venueIds: splitCsv(readParam(source, "venue")),
    artistSearch: readParam(source, "artist")?.trim() || null,
    priceMax: Number.isFinite(priceMaxNum) && priceMaxNum >= 0 ? priceMaxNum : null,
    freeOnly: parseBoolParam(readParam(source, "free")),
    availableOnly: parseBoolParam(readParam(source, "available")),
    dateFrom: parseDateParam(readParam(source, "date_from")),
    dateTo: parseDateParam(readParam(source, "date_to")),
  };
}

/**
 * Encode an EventFilters into URL params, mutating ``target`` in place.
 *
 * Empty/falsy fields are *removed* from ``target`` rather than written
 * as empty strings, so the URL stays clean. This is destructive on the
 * filter keys but leaves unrelated params (``city``, ``page``, ``view``,
 * ``window``, ``date``, ``month``) untouched — callers compose by
 * starting from the existing params and overlaying new filter state.
 */
export function applyFiltersToParams(
  target: URLSearchParams,
  filters: EventFilters,
): URLSearchParams {
  for (const key of FILTER_PARAM_KEYS) {
    target.delete(key);
  }
  if (filters.genres.length > 0) {
    target.set("genre", filters.genres.join(","));
  }
  if (filters.venueIds.length > 0) {
    target.set("venue", filters.venueIds.join(","));
  }
  if (filters.artistSearch && filters.artistSearch.length > 0) {
    target.set("artist", filters.artistSearch);
  }
  if (filters.freeOnly) {
    target.set("free", "1");
  } else if (filters.priceMax !== null) {
    target.set("price_max", String(filters.priceMax));
  }
  if (filters.availableOnly) {
    target.set("available", "1");
  }
  if (filters.dateFrom) {
    target.set("date_from", filters.dateFrom);
  }
  if (filters.dateTo) {
    target.set("date_to", filters.dateTo);
  }
  return target;
}

/** Number of distinct active filter dimensions — used for the trigger badge. */
export function countActiveFilters(filters: EventFilters): number {
  let n = 0;
  if (filters.genres.length > 0) n++;
  if (filters.venueIds.length > 0) n++;
  if (filters.artistSearch) n++;
  if (filters.priceMax !== null || filters.freeOnly) n++;
  if (filters.availableOnly) n++;
  if (filters.dateFrom || filters.dateTo) n++;
  return n;
}

/** True when no filter dimension is set. */
export function isEmptyFilters(filters: EventFilters): boolean {
  return countActiveFilters(filters) === 0;
}

/**
 * Return a copy of ``filters`` with the given dimension cleared.
 *
 * Used by the chip-row "X" buttons. ``"price"`` clears both the
 * numeric cap and the free-only flag in one go, since the UI treats
 * them as one logical control.
 */
export function clearFilterDimension(
  filters: EventFilters,
  dimension:
    | "genres"
    | "venueIds"
    | "artistSearch"
    | "price"
    | "availableOnly"
    | "dateRange",
): EventFilters {
  switch (dimension) {
    case "genres":
      return { ...filters, genres: [] };
    case "venueIds":
      return { ...filters, venueIds: [] };
    case "artistSearch":
      return { ...filters, artistSearch: null };
    case "price":
      return { ...filters, priceMax: null, freeOnly: false };
    case "availableOnly":
      return { ...filters, availableOnly: false };
    case "dateRange":
      return { ...filters, dateFrom: null, dateTo: null };
  }
}
