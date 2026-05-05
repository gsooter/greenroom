/**
 * Venue detail page — `/venues/[slug]` (server-side rendered).
 *
 * Shows the venue's core info plus its upcoming event lineup. Emits
 * `MusicVenue` JSON-LD so search and AI crawlers can index each venue
 * with address and geo coordinates.
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";

import EventCard from "@/components/events/EventCard";
import EmptyState from "@/components/ui/EmptyState";
import ExternalLinkIcon from "@/components/ui/ExternalLinkIcon";
import RegionBadge from "@/components/ui/RegionBadge";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import VenueStructuredData from "@/components/seo/VenueStructuredData";
import GetDirectionsButton from "@/components/venues/GetDirectionsButton";
import VenueSurroundings from "@/components/venues/VenueSurroundings";
import VenueTipsAnchor from "@/components/venues/VenueTipsAnchor";
import { ApiNotFoundError } from "@/lib/api/client";
import {
  getVenueBySlug,
  getVenueMapSnapshot,
  getVenueNearbyPois,
} from "@/lib/api/venues";
import { absolutePageUrl, buildVenueDetailMetadata } from "@/lib/metadata";

export const revalidate = 600;

interface VenueDetailPageProps {
  params: { slug: string };
}

export async function generateMetadata({
  params,
}: VenueDetailPageProps): Promise<Metadata> {
  try {
    const venue = await getVenueBySlug(params.slug, 600);
    return buildVenueDetailMetadata(venue);
  } catch {
    return {};
  }
}

export default async function VenueDetailPage({
  params,
}: VenueDetailPageProps) {
  let venue;
  try {
    venue = await getVenueBySlug(params.slug, 600);
  } catch (err) {
    if (err instanceof ApiNotFoundError) notFound();
    throw err;
  }

  const canonical = absolutePageUrl(`/venues/${venue.slug}`);

  const hasCoords = venue.latitude !== null && venue.longitude !== null;
  const [snapshot, nearbyPois] = hasCoords
    ? await Promise.all([
        getVenueMapSnapshot({
          slug: venue.slug,
          width: 800,
          height: 280,
        }),
        getVenueNearbyPois({ slug: venue.slug, limit: 12 }),
      ])
    : [null, []];

  return (
    <>
      <VenueStructuredData venue={venue} canonicalUrl={canonical} />
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Venues", url: absolutePageUrl("/venues") },
          { name: venue.name, url: canonical },
        ]}
      />

      <article className="flex flex-col gap-8 py-4">
        <div className="flex flex-col gap-4 sm:flex-row">
          {venue.image_url ? (
            <div
              className="aspect-[16/9] w-full rounded-lg bg-border/60 sm:w-1/2"
              style={{
                backgroundImage: `url(${venue.image_url})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }}
              role="presentation"
            />
          ) : (
            <div
              className="flex aspect-[16/9] w-full items-center justify-center rounded-lg bg-green-dark px-6 sm:w-1/2"
              role="presentation"
            >
              <span className="text-center text-2xl font-semibold leading-tight text-text-inverse">
                {venue.name}
              </span>
            </div>
          )}
          <div className="flex flex-1 flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <RegionBadge city={venue.city} />
              {venue.capacity ? (
                <span className="text-xs text-muted">
                  Capacity {venue.capacity.toLocaleString()}
                </span>
              ) : null}
            </div>
            <h1 className="text-3xl font-bold leading-tight sm:text-4xl">
              {venue.name}
            </h1>
            {venue.address ? (
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
                  `${venue.name} ${venue.address}`,
                )}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-muted hover:text-accent hover:underline"
              >
                {venue.address}
              </a>
            ) : null}
            {venue.description ? (
              <p className="text-sm text-foreground">{venue.description}</p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {venue.latitude !== null && venue.longitude !== null ? (
                <GetDirectionsButton
                  venueName={venue.name}
                  latitude={venue.latitude}
                  longitude={venue.longitude}
                  address={venue.address}
                />
              ) : null}
              {venue.website_url ? (
                <a
                  href={venue.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:border-accent hover:text-accent"
                >
                  Visit website
                  <ExternalLinkIcon />
                </a>
              ) : null}
            </div>
            <VenueTipsAnchor slug={venue.slug} />
          </div>
        </div>

        {hasCoords ? (
          <VenueSurroundings
            slug={venue.slug}
            venueId={venue.id}
            venueName={venue.name}
            venueAddress={venue.address ?? null}
            latitude={venue.latitude as number}
            longitude={venue.longitude as number}
            snapshot={snapshot}
            nearbyPois={nearbyPois}
          />
        ) : null}

        <section className="flex flex-col gap-4">
          <div className="flex items-end justify-between">
            <h2 className="text-xl font-semibold">
              Upcoming shows
              {venue.upcoming_event_count > 0
                ? ` (${venue.upcoming_event_count})`
                : ""}
            </h2>
            <Link
              href={{
                pathname: "/events",
                query: { city: venue.city?.slug ?? "" },
              }}
              className="text-sm font-medium text-accent hover:underline"
            >
              See more →
            </Link>
          </div>

          {venue.upcoming_events.length === 0 ? (
            <EmptyState
              title="No shows announced yet"
              description="Check back later — the overnight scraper refreshes this nightly."
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {venue.upcoming_events.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </div>
          )}
        </section>

      </article>
    </>
  );
}
