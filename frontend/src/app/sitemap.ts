/**
 * Dynamic sitemap — fetches every event and venue from the API.
 *
 * Runs at build time and then re-revalidates with the rest of the
 * static routes. Static marketing routes (home, /events, /venues) get
 * high priority; detail pages get daily freshness since listings turn
 * over every night.
 */

import type { MetadataRoute } from "next";

import { listEvents } from "@/lib/api/events";
import { listVenues } from "@/lib/api/venues";
import { absolutePageUrl } from "@/lib/metadata";
import type { EventSummary, VenueSummary } from "@/types";

export const revalidate = 3600;

const MAX_EVENTS = 500;
const PER_PAGE = 100;

async function collectAllEvents(): Promise<EventSummary[]> {
  const all: EventSummary[] = [];
  let page = 1;
  while (all.length < MAX_EVENTS) {
    let result;
    try {
      result = await listEvents({
        region: "DMV",
        page,
        perPage: PER_PAGE,
        revalidateSeconds: 3600,
      });
    } catch {
      break;
    }
    all.push(...result.data);
    if (!result.meta.has_next) break;
    page += 1;
  }
  return all.slice(0, MAX_EVENTS);
}

async function collectAllVenues(): Promise<VenueSummary[]> {
  try {
    const result = await listVenues({
      region: "DMV",
      perPage: PER_PAGE,
      revalidateSeconds: 3600,
    });
    return result.data;
  } catch {
    return [];
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [events, venues] = await Promise.all([
    collectAllEvents(),
    collectAllVenues(),
  ]);

  const now = new Date();

  const staticEntries: MetadataRoute.Sitemap = [
    {
      url: absolutePageUrl("/"),
      lastModified: now,
      changeFrequency: "weekly",
      priority: 1.0,
    },
    {
      url: absolutePageUrl("/events"),
      lastModified: now,
      changeFrequency: "daily",
      priority: 0.8,
    },
    {
      url: absolutePageUrl("/venues"),
      lastModified: now,
      changeFrequency: "weekly",
      priority: 0.8,
    },
  ];

  const eventEntries: MetadataRoute.Sitemap = events.map((event) => ({
    url: absolutePageUrl(`/events/${event.slug}`),
    lastModified: event.starts_at ? new Date(event.starts_at) : now,
    changeFrequency: "daily",
    priority: 0.9,
  }));

  const venueEntries: MetadataRoute.Sitemap = venues.map((venue) => ({
    url: absolutePageUrl(`/venues/${venue.slug}`),
    lastModified: now,
    changeFrequency: "weekly",
    priority: 0.8,
  }));

  return [...staticEntries, ...eventEntries, ...venueEntries];
}
