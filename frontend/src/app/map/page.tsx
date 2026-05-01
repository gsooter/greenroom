/**
 * Unified DC Map — ``/map`` (server-side rendered shell).
 *
 * Hosts two sub-views behind a single route so the new mobile bottom
 * nav can collapse "Tonight" and "Near Me" into one Map tab:
 *
 *   - ``/map`` (default) or ``/map?view=tonight`` — the existing
 *     tonight-on-the-map experience. Server-fetched pin envelope and
 *     community recommendations, rendered through ``TonightMapShell``.
 *   - ``/map?view=near-me`` — geolocation-driven nearby shows. No
 *     server data; ``NearMeShell`` owns the browser permission flow.
 *
 * The HTML rendered here always contains the event list (via the map
 * fallback on SSR for the Tonight view), which keeps the page indexable
 * by AI crawlers.
 */

import type { Metadata } from "next";

import MapViewToggle, { type MapView } from "@/components/map/MapViewToggle";
import NearMeShell from "@/components/map/NearMeShell";
import TonightMapShell from "@/components/map/TonightMapShell";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { getMapRecommendations, getTonightMap } from "@/lib/api/maps";
import { absolutePageUrl, buildPageMetadata } from "@/lib/metadata";
import type { MapRecommendation, TonightMapEnvelope } from "@/types";

// The /map shell fetches "tonight's" events, so a cached render pins the
// date to whenever the page was last regenerated. Force-dynamic avoids that.
export const dynamic = "force-dynamic";

// DMV bounding box — wide enough to cover Baltimore → Charlottesville.
const DMV_BBOX = {
  swLat: 38.55,
  swLng: -77.65,
  neLat: 39.45,
  neLng: -76.4,
};

interface MapPageProps {
  searchParams?: { view?: string };
}

/**
 * Resolves the requested view from the ``view`` query param.
 *
 * Args:
 *     raw: The raw query value (or undefined if not supplied).
 *
 * Returns:
 *     ``"near-me"`` if the param explicitly requests it, otherwise
 *     ``"tonight"`` (the default view).
 */
function resolveView(raw: string | undefined): MapView {
  return raw === "near-me" ? "near-me" : "tonight";
}

export function generateMetadata({
  searchParams,
}: MapPageProps = {}): Metadata {
  const view = resolveView(searchParams?.view);
  if (view === "near-me") {
    return buildPageMetadata({
      title: "Shows Near Me — Greenroom",
      description:
        "Find DMV concerts happening near you. Share your location and see nearby shows on a map or list, sorted nearest-first, for tonight or this week.",
      path: "/map?view=near-me",
    });
  }
  return buildPageMetadata({
    title: "Tonight's DC Map — Greenroom",
    description:
      "See every DMV concert happening tonight on a live map. Pins are color-coded by genre so you can skim the whole city at a glance.",
    path: "/map",
  });
}

async function loadInitialPins(): Promise<TonightMapEnvelope> {
  try {
    return await getTonightMap({ revalidateSeconds: 300 });
  } catch {
    return {
      data: [],
      meta: { count: 0, date: new Date().toISOString().slice(0, 10) },
    };
  }
}

async function loadRecommendations(): Promise<MapRecommendation[]> {
  try {
    return await getMapRecommendations({
      ...DMV_BBOX,
      sort: "top",
      limit: 120,
      revalidateSeconds: 300,
    });
  } catch {
    return [];
  }
}

export default async function MapPage({
  searchParams,
}: MapPageProps): Promise<JSX.Element> {
  const view = resolveView(searchParams?.view);

  if (view === "near-me") {
    return (
      <>
        <BreadcrumbStructuredData
          items={[
            { name: "Home", url: absolutePageUrl("/") },
            { name: "Map", url: absolutePageUrl("/map") },
            { name: "Near Me", url: absolutePageUrl("/map?view=near-me") },
          ]}
        />

        <section className="flex flex-col gap-4 pb-6 pt-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-bold sm:text-3xl">Shows near you</h1>
            <p className="text-sm text-muted">
              Share your location and we&apos;ll pull every DMV concert
              happening around you, sorted nearest-first.
            </p>
          </div>
          <MapViewToggle active="near-me" />
        </section>

        <NearMeShell />
      </>
    );
  }

  const [envelope, recommendations] = await Promise.all([
    loadInitialPins(),
    loadRecommendations(),
  ]);

  return (
    <>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Tonight", url: absolutePageUrl("/map") },
        ]}
      />

      <section className="flex flex-col gap-4 pb-6 pt-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold sm:text-3xl">Tonight on the map</h1>
          <p className="text-sm text-muted">
            {envelope.data.length > 0
              ? `${envelope.data.length} pinnable shows tonight across the DMV.`
              : "No mappable shows for tonight — the crawler may still be running."}
          </p>
        </div>
        <MapViewToggle active="tonight" />
      </section>

      <TonightMapShell
        initialEvents={envelope.data}
        recommendations={recommendations}
      />
    </>
  );
}
