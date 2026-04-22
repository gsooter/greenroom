/**
 * Small pure formatting helpers used by cards and detail pages.
 *
 * All functions accept `null | undefined` and return a sensible
 * fallback string so callers don't have to pre-guard display values.
 *
 * Time- and date-formatting helpers accept an optional `timeZone` IANA
 * string. Callers that don't pass one get ET, the canonical zone for all
 * DMV shows. Client components that expose a user-selectable viewing
 * timezone should pipe the preference through.
 */

const DEFAULT_TIME_ZONE = "America/New_York";

function buildDateFormatter(timeZone: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone,
  });
}

function buildTimeFormatter(timeZone: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  });
}

function buildLongDateFormatter(timeZone: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone,
  });
}

const DEFAULT_DATE_FORMATTER = buildDateFormatter(DEFAULT_TIME_ZONE);
const DEFAULT_TIME_FORMATTER = buildTimeFormatter(DEFAULT_TIME_ZONE);
const DEFAULT_LONG_DATE_FORMATTER = buildLongDateFormatter(DEFAULT_TIME_ZONE);

function formatterFor(
  defaultFormatter: Intl.DateTimeFormat,
  builder: (tz: string) => Intl.DateTimeFormat,
  timeZone: string | undefined,
): Intl.DateTimeFormat {
  if (!timeZone || timeZone === DEFAULT_TIME_ZONE) return defaultFormatter;
  try {
    return builder(timeZone);
  } catch {
    return defaultFormatter;
  }
}

export function formatEventDate(
  iso: string | null,
  timeZone?: string,
): string {
  if (!iso) return "Date TBA";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Date TBA";
  return formatterFor(DEFAULT_DATE_FORMATTER, buildDateFormatter, timeZone).format(d);
}

export function formatEventTime(
  iso: string | null,
  timeZone?: string,
): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return formatterFor(DEFAULT_TIME_FORMATTER, buildTimeFormatter, timeZone).format(d);
}

export function formatLongDate(
  iso: string | null,
  timeZone?: string,
): string {
  if (!iso) return "Date TBA";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Date TBA";
  return formatterFor(DEFAULT_LONG_DATE_FORMATTER, buildLongDateFormatter, timeZone).format(d);
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
