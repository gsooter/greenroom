/**
 * Dismissible chip row showing currently-applied event filters.
 *
 * Renders one chip per active dimension (genres, venues, artist, price,
 * availability, custom date range). Each chip is a plain ``<Link>`` that
 * drops just that dimension from the URL, so the row works without JS
 * and survives SSR. ``WindowFilterChips`` continues to own the
 * preset-window row above this — these chips are only for the panel's
 * extra dimensions.
 */

import Link from "next/link";

import {
  applyFiltersToParams,
  clearFilterDimension,
  isEmptyFilters,
  type EventFilters,
} from "@/lib/event-filters";

interface ActiveFilterChipsProps {
  filters: EventFilters;
  /** Pre-built params holding the non-filter context (city, view, etc.). */
  baseParams: URLSearchParams;
  /** Lookup for human labels — slug → display name for genres. */
  genreLabels: Record<string, string>;
  /** Lookup for human labels — UUID → display name for venues. */
  venueLabels: Record<string, string>;
}

interface Chip {
  /** Stable key for React. */
  key: string;
  /** Visible text on the chip. */
  label: string;
  /** Filters with this chip's dimension cleared. */
  next: EventFilters;
}

function buildChips(
  filters: EventFilters,
  genreLabels: Record<string, string>,
  venueLabels: Record<string, string>,
): Chip[] {
  const chips: Chip[] = [];

  if (filters.genres.length > 0) {
    const named = filters.genres.map((slug) => genreLabels[slug] ?? slug);
    chips.push({
      key: "genres",
      label:
        filters.genres.length === 1
          ? `Genre: ${named[0]}`
          : `Genres: ${named.join(", ")}`,
      next: clearFilterDimension(filters, "genres"),
    });
  }

  if (filters.venueIds.length > 0) {
    const named = filters.venueIds.map((id) => venueLabels[id] ?? "Venue");
    chips.push({
      key: "venues",
      label:
        filters.venueIds.length === 1
          ? `Venue: ${named[0]}`
          : `Venues: ${filters.venueIds.length}`,
      next: clearFilterDimension(filters, "venueIds"),
    });
  }

  if (filters.artistSearch) {
    chips.push({
      key: "artist",
      label: `Artist: ${filters.artistSearch}`,
      next: clearFilterDimension(filters, "artistSearch"),
    });
  }

  if (filters.freeOnly) {
    chips.push({
      key: "price",
      label: "Free shows only",
      next: clearFilterDimension(filters, "price"),
    });
  } else if (filters.priceMax !== null) {
    chips.push({
      key: "price",
      label: `Under $${filters.priceMax}`,
      next: clearFilterDimension(filters, "price"),
    });
  }

  if (filters.availableOnly) {
    chips.push({
      key: "available",
      label: "Available only",
      next: clearFilterDimension(filters, "availableOnly"),
    });
  }

  if (filters.dateFrom || filters.dateTo) {
    const range =
      filters.dateFrom && filters.dateTo
        ? `${filters.dateFrom} → ${filters.dateTo}`
        : filters.dateFrom
          ? `From ${filters.dateFrom}`
          : `Through ${filters.dateTo}`;
    chips.push({
      key: "dateRange",
      label: `Date: ${range}`,
      next: clearFilterDimension(filters, "dateRange"),
    });
  }

  return chips;
}

function buildHref(
  baseParams: URLSearchParams,
  filters: EventFilters,
): string {
  const out = new URLSearchParams(baseParams);
  applyFiltersToParams(out, filters);
  const qs = out.toString();
  return qs ? `/events?${qs}` : "/events";
}

export default function ActiveFilterChips({
  filters,
  baseParams,
  genreLabels,
  venueLabels,
}: ActiveFilterChipsProps): JSX.Element | null {
  if (isEmptyFilters(filters)) return null;
  const chips = buildChips(filters, genreLabels, venueLabels);
  if (chips.length === 0) return null;

  const clearAllHref = buildHref(baseParams, {
    genres: [],
    venueIds: [],
    artistSearch: null,
    priceMax: null,
    freeOnly: false,
    availableOnly: false,
    dateFrom: null,
    dateTo: null,
  });

  return (
    <div className="flex flex-wrap items-center gap-2">
      {chips.map((chip) => (
        <Link
          key={chip.key}
          href={buildHref(baseParams, chip.next)}
          aria-label={`Remove filter: ${chip.label}`}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-sm text-foreground transition hover:border-accent hover:text-accent"
        >
          <span>{chip.label}</span>
          <span aria-hidden="true" className="text-muted">
            ×
          </span>
        </Link>
      ))}
      {chips.length > 1 ? (
        <Link
          href={clearAllHref}
          className="text-sm text-muted underline-offset-2 hover:text-accent hover:underline"
        >
          Clear all
        </Link>
      ) : null}
    </div>
  );
}
