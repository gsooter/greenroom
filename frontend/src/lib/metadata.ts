/**
 * Helpers for building Next.js `Metadata` objects.
 *
 * Every public page must export a `generateMetadata` (CLAUDE.md rule).
 * These helpers produce consistent titles, descriptions, and Open
 * Graph tags so pages don't each re-invent the structure.
 */

import type { Metadata } from "next";

import { config } from "@/lib/config";
import { formatLongDate, formatPriceRange } from "@/lib/format";
import type { EventDetail, VenueDetail } from "@/types";

const SITE_NAME = "Greenroom";
const SITE_TAGLINE =
  "The DMV's concert calendar with Spotify-powered recommendations.";

function absoluteUrl(path: string): string {
  const base = config.baseUrl.replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export interface BasePageMetadataInput {
  title: string;
  description: string;
  path: string;
  image?: string | null;
}

export function buildPageMetadata(input: BasePageMetadataInput): Metadata {
  const url = absoluteUrl(input.path);
  const images = input.image ? [{ url: input.image }] : undefined;
  return {
    title: input.title,
    description: input.description,
    alternates: { canonical: url },
    openGraph: {
      title: input.title,
      description: input.description,
      url,
      siteName: SITE_NAME,
      images,
      type: "website",
    },
    twitter: {
      card: images ? "summary_large_image" : "summary",
      title: input.title,
      description: input.description,
      images: input.image ? [input.image] : undefined,
    },
  };
}

export function buildHomeMetadata(): Metadata {
  return buildPageMetadata({
    title: `${SITE_NAME} — DMV Concert Calendar`,
    description: SITE_TAGLINE,
    path: "/",
  });
}

export function buildEventsIndexMetadata(cityName: string | null): Metadata {
  const scope = cityName ?? "the DMV";
  return buildPageMetadata({
    title: `Concerts in ${scope} — ${SITE_NAME}`,
    description: `Upcoming concerts, shows, and live music across ${scope}. Updated nightly from venue listings and Ticketmaster.`,
    path: "/events",
  });
}

export function buildVenuesIndexMetadata(cityName: string | null): Metadata {
  const scope = cityName ?? "the DMV";
  return buildPageMetadata({
    title: `Music Venues in ${scope} — ${SITE_NAME}`,
    description: `Directory of music venues across ${scope} with upcoming shows, capacity, and ticket links.`,
    path: "/venues",
  });
}

export function buildEventDetailMetadata(event: EventDetail): Metadata {
  const venueName = event.venue?.name ?? "TBA";
  const cityName = event.venue?.city?.name ?? "Washington DC";
  const dateLabel = formatLongDate(event.starts_at);
  const price = formatPriceRange(event.min_price, event.max_price);

  const description = [
    `${event.title} live at ${venueName} in ${cityName}.`,
    dateLabel,
    price ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  return buildPageMetadata({
    title: `${event.title} at ${venueName} — ${dateLabel}`,
    description,
    path: `/events/${event.slug}`,
    image: event.image_url,
  });
}

export function buildVenueDetailMetadata(venue: VenueDetail): Metadata {
  const cityName = venue.city?.name ?? "Washington DC";
  const state = venue.city?.state ?? "";
  const location = state ? `${cityName}, ${state}` : cityName;
  const description =
    venue.description ??
    `Upcoming concerts and shows at ${venue.name} in ${location}.`;
  return buildPageMetadata({
    title: `${venue.name} — ${location} Music Venue`,
    description,
    path: `/venues/${venue.slug}`,
    image: venue.image_url,
  });
}

export function absolutePageUrl(path: string): string {
  return absoluteUrl(path);
}
