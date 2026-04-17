/**
 * Events index — `/events` (server-side rendered).
 *
 * Paginated list of upcoming concerts across the DMV. Accepts a
 * `?city=<slug>` query param to narrow to a single city and `?page=<n>`
 * for pagination. The city picker in the top nav writes to the same
 * `city` param so filter state is fully URL-driven.
 */

import Link from "next/link";
import type { Metadata } from "next";

import AppShell from "@/components/layout/AppShell";
import EventCard from "@/components/events/EventCard";
import EmptyState from "@/components/ui/EmptyState";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { getCityBySlug } from "@/lib/api/cities";
import { listEvents } from "@/lib/api/events";
import {
  absolutePageUrl,
  buildEventsIndexMetadata,
} from "@/lib/metadata";
import type { City, EventSummary, Paginated } from "@/types";

export const revalidate = 300;

const PER_PAGE = 24;

interface EventsPageProps {
  searchParams: { city?: string; page?: string };
}

function parsePage(value: string | undefined): number {
  const n = value ? Number.parseInt(value, 10) : 1;
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

async function loadCity(slug: string | undefined): Promise<City | null> {
  if (!slug) return null;
  try {
    return await getCityBySlug(slug, 300);
  } catch {
    return null;
  }
}

export async function generateMetadata({
  searchParams,
}: EventsPageProps): Promise<Metadata> {
  const city = await loadCity(searchParams.city);
  return buildEventsIndexMetadata(city?.name ?? null);
}

export default async function EventsPage({ searchParams }: EventsPageProps) {
  const page = parsePage(searchParams.page);
  const city = await loadCity(searchParams.city);

  let results: Paginated<EventSummary> = {
    data: [],
    meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
  };

  try {
    results = await listEvents({
      region: city ? undefined : "DMV",
      cityId: city?.id,
      page,
      perPage: PER_PAGE,
      revalidateSeconds: 300,
    });
  } catch {
    results = {
      data: [],
      meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
    };
  }

  const heading = city ? `Concerts in ${city.name}` : "Concerts across the DMV";

  return (
    <AppShell selectedCitySlug={city?.slug ?? null}>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Events", url: absolutePageUrl("/events") },
        ]}
      />

      <section className="flex flex-col gap-2 pb-6 pt-4">
        <h1 className="text-2xl font-bold sm:text-3xl">{heading}</h1>
        <p className="text-sm text-muted">
          {results.meta.total > 0
            ? `Showing ${results.data.length} of ${results.meta.total} upcoming shows.`
            : "Updated nightly from venue websites and Ticketmaster."}
        </p>
      </section>

      {results.data.length === 0 ? (
        <EmptyState
          title="No shows match these filters"
          description="Try switching to a different city or view the whole DMV calendar."
        >
          <Link
            href="/events"
            className="mt-2 text-sm font-medium text-accent hover:underline"
          >
            View all DMV events →
          </Link>
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {results.data.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
          <Pagination
            page={results.meta.page}
            hasNext={results.meta.has_next}
            citySlug={city?.slug ?? null}
          />
        </>
      )}
    </AppShell>
  );
}

function Pagination({
  page,
  hasNext,
  citySlug,
}: {
  page: number;
  hasNext: boolean;
  citySlug: string | null;
}) {
  const base = "/events";
  const qs = (p: number) => {
    const params = new URLSearchParams();
    if (citySlug) params.set("city", citySlug);
    if (p > 1) params.set("page", String(p));
    const q = params.toString();
    return q ? `${base}?${q}` : base;
  };

  return (
    <nav
      aria-label="Pagination"
      className="flex items-center justify-between pt-8"
    >
      {page > 1 ? (
        <Link
          href={qs(page - 1)}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
        >
          ← Previous
        </Link>
      ) : (
        <span />
      )}
      <span className="text-sm text-muted">Page {page}</span>
      {hasNext ? (
        <Link
          href={qs(page + 1)}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
        >
          Next →
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
