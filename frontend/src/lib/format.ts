/**
 * Small pure formatting helpers used by cards and detail pages.
 *
 * All functions accept `null | undefined` and return a sensible
 * fallback string so callers don't have to pre-guard display values.
 */

const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  timeZone: "America/New_York",
});

const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

const LONG_DATE_FORMATTER = new Intl.DateTimeFormat("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "America/New_York",
});

export function formatEventDate(iso: string | null): string {
  if (!iso) return "Date TBA";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Date TBA";
  return DATE_FORMATTER.format(d);
}

export function formatEventTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return TIME_FORMATTER.format(d);
}

export function formatLongDate(iso: string | null): string {
  if (!iso) return "Date TBA";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Date TBA";
  return LONG_DATE_FORMATTER.format(d);
}

export function formatPriceRange(
  min: number | null,
  max: number | null,
): string | null {
  if (min == null && max == null) return null;
  if (min != null && max != null && min !== max) {
    return `$${Math.round(min)}–$${Math.round(max)}`;
  }
  const single = min ?? max;
  return single == null ? null : `From $${Math.round(single)}`;
}

export function joinArtists(
  artists: string[] | null | undefined,
  limit = 3,
): string | null {
  if (!artists || artists.length === 0) return null;
  if (artists.length <= limit) return artists.join(", ");
  return `${artists.slice(0, limit).join(", ")} +${artists.length - limit} more`;
}
