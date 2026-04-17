/**
 * Home page — `/` (server-side rendered).
 *
 * Highlights upcoming DMV shows, links into the Events and Venues
 * indexes, and emits site-level JSON-LD so Google and AI crawlers can
 * identify Greenroom as the DMV concert calendar. Public browse — no
 * auth required.
 */

import Link from "next/link";
import type { Metadata } from "next";

import AppShell from "@/components/layout/AppShell";
import EventCard from "@/components/events/EventCard";
import EmptyState from "@/components/ui/EmptyState";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import SiteStructuredData from "@/components/seo/SiteStructuredData";
import { listEvents } from "@/lib/api/events";
import { absolutePageUrl, buildHomeMetadata } from "@/lib/metadata";
import type { EventSummary } from "@/types";

export const revalidate = 300;

export function generateMetadata(): Metadata {
  return buildHomeMetadata();
}

export default async function HomePage() {
  let upcoming: EventSummary[] = [];
  try {
    const result = await listEvents({
      region: "DMV",
      perPage: 8,
      revalidateSeconds: 300,
    });
    upcoming = result.data;
  } catch {
    upcoming = [];
  }

  return (
    <AppShell>
      <SiteStructuredData />
      <BreadcrumbStructuredData
        items={[{ name: "Home", url: absolutePageUrl("/") }]}
      />

      <section className="flex flex-col gap-4 py-8 sm:py-12">
        <p className="text-sm font-semibold uppercase tracking-widest text-accent">
          DMV · DC · MD · VA
        </p>
        <h1 className="text-3xl font-bold leading-tight sm:text-5xl">
          Every DMV concert in one calendar.
        </h1>
        <p className="max-w-2xl text-base text-muted sm:text-lg">
          Greenroom aggregates shows from every major venue in Washington DC,
          Maryland, and Virginia — updated nightly. Sign in with Spotify for
          recommendations tuned to what you already listen to.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <Link
            href="/events"
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90"
          >
            Browse events
          </Link>
          <Link
            href="/venues"
            className="rounded-md border border-border px-4 py-2 text-sm font-semibold text-foreground hover:border-accent hover:text-accent"
          >
            Explore venues
          </Link>
        </div>
      </section>

      <section className="flex flex-col gap-4 pb-10">
        <div className="flex items-end justify-between">
          <h2 className="text-xl font-semibold">Upcoming across the DMV</h2>
          <Link
            href="/events"
            className="text-sm font-medium text-accent hover:underline"
          >
            See all →
          </Link>
        </div>

        {upcoming.length === 0 ? (
          <EmptyState
            title="No upcoming shows yet"
            description="The overnight scraper will populate events shortly. Check back soon."
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {upcoming.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
