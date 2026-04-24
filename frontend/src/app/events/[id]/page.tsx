/**
 * Event detail page — `/events/[id]` (server-side rendered).
 *
 * `[id]` accepts either a UUID or slug — the backend route does the
 * right thing for either. The slug form is canonical for URLs. Emits
 * `MusicEvent` JSON-LD so this page is eligible for Google event rich
 * results.
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";

import SaveEventButton from "@/components/events/SaveEventButton";
import EmptyState from "@/components/ui/EmptyState";
import RegionBadge from "@/components/ui/RegionBadge";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import EventStructuredData from "@/components/seo/EventStructuredData";
import { ApiNotFoundError } from "@/lib/api/client";
import { getEvent } from "@/lib/api/events";
import { getVenueBySlug } from "@/lib/api/venues";
import {
  formatEventTime,
  formatLongDate,
  formatPriceRange,
  joinArtists,
} from "@/lib/format";
import {
  absolutePageUrl,
  buildEventDetailMetadata,
} from "@/lib/metadata";

export const revalidate = 300;

interface EventDetailPageProps {
  params: { id: string };
}

function decodeParamId(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export async function generateMetadata({
  params,
}: EventDetailPageProps): Promise<Metadata> {
  try {
    const event = await getEvent(decodeParamId(params.id), 300);
    return buildEventDetailMetadata(event);
  } catch {
    return {};
  }
}

export default async function EventDetailPage({
  params,
}: EventDetailPageProps) {
  let event;
  try {
    event = await getEvent(decodeParamId(params.id), 300);
  } catch (err) {
    if (err instanceof ApiNotFoundError) notFound();
    throw err;
  }

  const venueSlug = event.venue?.slug;
  const venue = venueSlug ? await safeGetVenue(venueSlug) : null;

  const dateLabel = formatLongDate(event.starts_at);
  const startTime = formatEventTime(event.starts_at);
  const doorsTime = formatEventTime(event.doors_at);
  const priceRange = formatPriceRange(event.min_price, event.max_price);
  const artistLine = joinArtists(event.artists, 10);
  const canonical = absolutePageUrl(`/events/${event.slug}`);

  return (
    <>
      {venue ? (
        <EventStructuredData
          event={event}
          venue={venue}
          canonicalUrl={canonical}
        />
      ) : null}
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Events", url: absolutePageUrl("/events") },
          { name: event.title, url: canonical },
        ]}
      />

      <article className="flex flex-col gap-8 py-4">
        <div className="flex flex-col gap-4 sm:flex-row">
          <div
            className="aspect-[16/9] w-full rounded-lg bg-border/60 sm:w-1/2"
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
          <div className="flex flex-1 flex-col gap-3">
            <p className="text-sm font-semibold uppercase tracking-wide text-accent">
              {dateLabel}
              {startTime ? ` · ${startTime}` : ""}
            </p>
            <h1 className="text-3xl font-bold leading-tight sm:text-4xl">
              {event.title}
            </h1>
            {artistLine ? (
              <p className="text-base text-muted">{artistLine}</p>
            ) : null}
            {event.venue ? (
              <div className="flex flex-wrap items-center gap-2 pt-1 text-sm">
                <Link
                  href={`/venues/${event.venue.slug}`}
                  className="font-medium text-foreground hover:text-accent"
                >
                  {event.venue.name}
                </Link>
                <RegionBadge city={event.venue.city} />
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {event.ticket_url ? (
                <a
                  href={event.ticket_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90"
                >
                  Get tickets
                </a>
              ) : null}
              <SaveEventButton eventId={event.id} variant="pill" />
              {priceRange ? (
                <span className="text-sm text-foreground">{priceRange}</span>
              ) : null}
              {event.status === "sold_out" ? (
                <span className="text-sm font-medium text-muted">
                  Sold out
                </span>
              ) : null}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
          <DetailItem label="Date" value={dateLabel} />
          <DetailItem
            label="Doors"
            value={doorsTime ?? (startTime ? `Show ${startTime}` : "TBA")}
          />
          <DetailItem
            label="Tickets"
            value={priceRange ?? "See ticket provider"}
          />
        </div>

        {event.description ? (
          <section className="flex flex-col gap-2">
            <h2 className="text-lg font-semibold">About the show</h2>
            <p className="whitespace-pre-line text-sm text-foreground">
              {event.description}
            </p>
          </section>
        ) : null}

        {event.genres.length > 0 ? (
          <section className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-muted">Genres:</span>
            {event.genres.map((genre) => (
              <span
                key={genre}
                className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground"
              >
                {genre}
              </span>
            ))}
          </section>
        ) : null}

        {!event.venue ? (
          <EmptyState
            title="Venue details unavailable"
            description="This event isn't linked to a venue page yet."
          />
        ) : null}
      </article>
    </>
  );
}

async function safeGetVenue(slug: string) {
  try {
    return await getVenueBySlug(slug, 300);
  } catch {
    return null;
  }
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface/70 p-4">
      <span className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </span>
      <span className="text-sm font-semibold text-foreground">{value}</span>
    </div>
  );
}
