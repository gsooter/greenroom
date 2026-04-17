/**
 * Event card used in browse and list views.
 *
 * Server component — renders an `EventSummary` as a clickable tile
 * that links to the event detail page. Shows headline, artists, date,
 * venue, and (when available) price range and status.
 */

import Link from "next/link";

import RegionBadge from "@/components/ui/RegionBadge";
import {
  formatEventDate,
  formatEventTime,
  formatPriceRange,
  joinArtists,
} from "@/lib/format";
import type { EventStatus, EventSummary } from "@/types";

interface EventCardProps {
  event: EventSummary;
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
  cancelled: "bg-red-500/10 text-red-700 dark:text-red-300",
  postponed: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

export default function EventCard({ event }: EventCardProps) {
  const artists = joinArtists(event.artists);
  const date = formatEventDate(event.starts_at);
  const time = formatEventTime(event.starts_at);
  const price = formatPriceRange(event.min_price, event.max_price);
  const venue = event.venue;

  return (
    <Link
      href={`/events/${event.slug}`}
      className="group flex flex-col overflow-hidden rounded-lg border border-border bg-surface transition hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
    >
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
          <span className="text-xs font-semibold uppercase tracking-wide text-accent">
            {date}
            {time ? ` · ${time}` : ""}
          </span>
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
          <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
            {venue ? <span className="font-medium">{venue.name}</span> : null}
            {venue ? <RegionBadge city={venue.city} /> : null}
          </div>
          {price ? (
            <span className="text-sm font-medium text-foreground">{price}</span>
          ) : null}
        </div>
      </div>
    </Link>
  );
}
