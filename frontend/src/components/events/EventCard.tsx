/**
 * Event card used in browse and list views.
 *
 * Renders an `EventSummary` as a tile. A full-card `<Link>` sits on top
 * of the visual content to capture clicks and jump to the detail page;
 * the save button sits above the link at a higher z-index so it can
 * intercept its own click without navigating away.
 *
 * The ``compact`` prop swaps the default vertical hero-image layout for
 * a single-row list item with a small square thumbnail (or no image) so
 * a long page of cards fits in a fraction of the scrolling. Both
 * variants share the same link/save-button overlay so accessibility
 * and click semantics stay identical.
 */

import Link from "next/link";

import EventDateTime from "@/components/events/EventDateTime";
import SaveEventButton from "@/components/events/SaveEventButton";
import RegionBadge from "@/components/ui/RegionBadge";
import { formatPriceRange, formatRelativeTime, joinArtists } from "@/lib/format";
import type { EventStatus, EventSummary } from "@/types";

interface EventCardProps {
  event: EventSummary;
  /**
   * When true, render the single-row compact layout. Defaults to the
   * existing vertical layout so callers that don't opt in are
   * unaffected.
   */
  compact?: boolean;
}

const STATUS_LABEL: Record<EventStatus, string> = {
  announced: "Announced",
  on_sale: "On sale",
  confirmed: "Confirmed",
  sold_out: "Sold out",
  cancelled: "Cancelled",
  postponed: "Postponed",
};

const STATUS_CLASS: Record<EventStatus, string> = {
  announced: "bg-border text-muted",
  on_sale: "bg-accent/15 text-accent",
  confirmed: "bg-accent/15 text-accent",
  sold_out: "bg-border text-foreground",
  cancelled: "bg-blush-soft text-blush-accent",
  postponed: "bg-navy-soft text-navy-dark",
};

export default function EventCard({ event, compact = false }: EventCardProps) {
  return compact ? (
    <CompactEventCard event={event} />
  ) : (
    <ComfortableEventCard event={event} />
  );
}

function ComfortableEventCard({ event }: { event: EventSummary }): JSX.Element {
  const artists = joinArtists(event.artists);
  const price = formatPriceRange(event.min_price, event.max_price);
  const priceAge =
    price && event.prices_refreshed_at
      ? formatRelativeTime(event.prices_refreshed_at)
      : null;
  const venue = event.venue;

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-lg border border-border bg-surface transition hover:border-accent focus-within:ring-2 focus-within:ring-accent">
      <div
        className="aspect-[16/9] w-full bg-border/60"
        style={
          event.image_url
            ? {
                backgroundImage: `url(${event.image_url})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }
            : undefined
        }
        role="presentation"
      />

      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <EventDateTime
            iso={event.starts_at}
            className="text-xs font-semibold uppercase tracking-wide text-accent"
          />
          <span
            className={
              "rounded-full px-2 py-0.5 text-xs font-medium " +
              STATUS_CLASS[event.status]
            }
          >
            {STATUS_LABEL[event.status]}
          </span>
        </div>

        <h3 className="line-clamp-2 text-base font-semibold leading-snug text-foreground group-hover:text-accent">
          {event.title}
        </h3>

        {artists ? (
          <p className="line-clamp-1 text-sm text-muted">{artists}</p>
        ) : null}

        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-2">
          <div className="relative z-20 flex flex-wrap items-center gap-2 text-sm text-foreground">
            {venue ? (
              <Link
                href={`/venues/${venue.slug}`}
                className="relative z-20 font-medium hover:text-accent hover:underline"
              >
                {venue.name}
              </Link>
            ) : null}
            {venue ? <RegionBadge city={venue.city} /> : null}
          </div>
          {price ? (
            <div className="flex flex-col items-end leading-tight">
              <span className="text-sm font-medium text-foreground">
                {price}
              </span>
              {priceAge ? (
                <span className="text-[10px] text-muted">
                  Updated {priceAge}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <Link
        href={`/events/${event.slug}`}
        className="absolute inset-0 z-10 focus:outline-none"
        aria-label={event.title}
      />

      <div className="absolute right-3 top-3 z-20">
        <SaveEventButton eventId={event.id} variant="icon" />
      </div>
    </div>
  );
}

function CompactEventCard({ event }: { event: EventSummary }): JSX.Element {
  const artists = joinArtists(event.artists);
  const price = formatPriceRange(event.min_price, event.max_price);
  const venue = event.venue;

  return (
    <div className="group relative flex flex-row items-stretch gap-3 overflow-hidden rounded-lg border border-border bg-surface p-2 pr-12 transition hover:border-accent focus-within:ring-2 focus-within:ring-accent">
      <div
        className="h-16 w-16 shrink-0 rounded-md bg-border/60 sm:h-20 sm:w-20"
        style={
          event.image_url
            ? {
                backgroundImage: `url(${event.image_url})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }
            : undefined
        }
        role="presentation"
      />

      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
        <div className="flex items-baseline gap-2">
          <EventDateTime
            iso={event.starts_at}
            className="text-[10px] font-semibold uppercase tracking-wide text-accent"
          />
          <span
            className={
              "rounded-full px-1.5 py-px text-[10px] font-medium " +
              STATUS_CLASS[event.status]
            }
          >
            {STATUS_LABEL[event.status]}
          </span>
        </div>

        <h3 className="line-clamp-1 text-sm font-semibold leading-snug text-foreground group-hover:text-accent">
          {event.title}
        </h3>

        <div className="relative z-20 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
          {artists ? <span className="line-clamp-1">{artists}</span> : null}
          {venue ? (
            <Link
              href={`/venues/${venue.slug}`}
              className="relative z-20 font-medium text-foreground hover:text-accent hover:underline"
            >
              {venue.name}
            </Link>
          ) : null}
          {price ? (
            <span className="text-foreground">{price}</span>
          ) : null}
        </div>
      </div>

      <Link
        href={`/events/${event.slug}`}
        className="absolute inset-0 z-10 focus:outline-none"
        aria-label={event.title}
      />

      <div className="absolute right-2 top-1/2 z-20 -translate-y-1/2">
        <SaveEventButton eventId={event.id} variant="icon" />
      </div>
    </div>
  );
}
