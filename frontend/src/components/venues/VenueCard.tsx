/**
 * Venue card used in the directory listing.
 *
 * Server component — renders a `VenueSummary` as a clickable tile that
 * links to the venue detail page. Displays image, name, tags, and
 * city/region badge.
 */

import Link from "next/link";

import RegionBadge from "@/components/ui/RegionBadge";
import type { VenueSummary } from "@/types";

interface VenueCardProps {
  venue: VenueSummary;
}

export default function VenueCard({ venue }: VenueCardProps) {
  const tags = venue.tags.slice(0, 3);
  return (
    <Link
      href={`/venues/${venue.slug}`}
      className="group flex flex-col overflow-hidden rounded-lg border border-border bg-surface transition hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
    >
      {venue.image_url ? (
        <div
          className="aspect-[16/9] w-full bg-border/60"
          style={{
            backgroundImage: `url(${venue.image_url})`,
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
          role="presentation"
        />
      ) : (
        <div
          className="flex aspect-[16/9] w-full items-center justify-center bg-green-dark px-4"
          role="presentation"
        >
          <span className="text-center text-lg font-semibold leading-tight text-text-inverse">
            {venue.name}
          </span>
        </div>
      )}

      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-base font-semibold leading-snug text-foreground group-hover:text-accent">
            {venue.name}
          </h3>
          <RegionBadge city={venue.city} />
        </div>

        {venue.address ? (
          <p className="line-clamp-1 text-sm text-muted">{venue.address}</p>
        ) : null}

        {tags.length > 0 ? (
          <div className="mt-auto flex flex-wrap gap-1 pt-2">
            {tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-border px-2 py-0.5 text-xs text-muted"
              >
                {tag}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Link>
  );
}
