/**
 * Venues directory — `/venues` (server-side rendered).
 *
 * Lists every active DMV venue with optional `?city=<slug>` narrowing.
 * Per CLAUDE.md, this page is public browse — no auth required.
 */

import Link from "next/link";
import type { Metadata } from "next";

import AppShell from "@/components/layout/AppShell";
import VenueCard from "@/components/venues/VenueCard";
import EmptyState from "@/components/ui/EmptyState";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { getCityBySlug } from "@/lib/api/cities";
import { listVenues } from "@/lib/api/venues";
import {
  absolutePageUrl,
  buildVenuesIndexMetadata,
} from "@/lib/metadata";
import type { City, Paginated, VenueSummary } from "@/types";

export const revalidate = 600;

const PER_PAGE = 48;

interface VenuesPageProps {
  searchParams: { city?: string; page?: string };
}

function parsePage(value: string | undefined): number {
  const n = value ? Number.parseInt(value, 10) : 1;
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

async function loadCity(slug: string | undefined): Promise<City | null> {
  if (!slug) return null;
  try {
    return await getCityBySlug(slug, 600);
  } catch {
    return null;
  }
}

export async function generateMetadata({
  searchParams,
}: VenuesPageProps): Promise<Metadata> {
  const city = await loadCity(searchParams.city);
  return buildVenuesIndexMetadata(city?.name ?? null);
}

export default async function VenuesPage({ searchParams }: VenuesPageProps) {
  const page = parsePage(searchParams.page);
  const city = await loadCity(searchParams.city);

  let results: Paginated<VenueSummary> = {
    data: [],
    meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
  };

  try {
    results = await listVenues({
      region: city ? undefined : "DMV",
      cityId: city?.id,
      page,
      perPage: PER_PAGE,
      revalidateSeconds: 600,
    });
  } catch {
    results = {
      data: [],
      meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
    };
  }

  const heading = city ? `Venues in ${city.name}` : "Venues across the DMV";

  return (
    <AppShell selectedCitySlug={city?.slug ?? null}>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Venues", url: absolutePageUrl("/venues") },
        ]}
      />

      <section className="flex flex-col gap-2 pb-6 pt-4">
        <h1 className="text-2xl font-bold sm:text-3xl">{heading}</h1>
        <p className="text-sm text-muted">
          {results.meta.total > 0
            ? `${results.meta.total} venues covered across DC, Maryland, and Virginia.`
            : "Venue directory for the DMV music scene."}
        </p>
      </section>

      {results.data.length === 0 ? (
        <EmptyState
          title="No venues listed for this city yet"
          description="Switch cities in the top nav or browse the full DMV directory."
        >
          <Link
            href="/venues"
            className="mt-2 text-sm font-medium text-accent hover:underline"
          >
            View all DMV venues →
          </Link>
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {results.data.map((venue) => (
            <VenueCard key={venue.id} venue={venue} />
          ))}
        </div>
      )}
    </AppShell>
  );
}
