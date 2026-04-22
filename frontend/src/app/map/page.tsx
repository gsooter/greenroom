/**
 * Tonight's DC Map — ``/map`` (server-side rendered shell).
 *
 * Server component that fetches tonight's pinnable DMV events and the
 * community-recommendation overlay before any client JS runs. The
 * interactive MapKit JS surface is mounted by :func:`TonightMapShell`,
 * a client component that layers the filter bar on top of
 * :func:`TonightMap`.
 *
 * The HTML rendered here always contains the event list (via the map
 * fallback on SSR), which keeps the page indexable by AI crawlers —
 * they see the same tonight's pins a user would see on a map.
 */

import type { Metadata } from "next";

import TonightMapShell from "@/components/map/TonightMapShell";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { getMapRecommendations, getTonightMap } from "@/lib/api/maps";
import { absolutePageUrl, buildPageMetadata } from "@/lib/metadata";
import type { MapRecommendation, TonightMapEnvelope } from "@/types";

export const revalidate = 300;

// DMV bounding box — wide enough to cover Baltimore → Charlottesville.
const DMV_BBOX = {
  swLat: 38.55,
  swLng: -77.65,
  neLat: 39.45,
  neLng: -76.4,
};

export function generateMetadata(): Metadata {
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
    return { data: [], meta: { count: 0, date: new Date().toISOString().slice(0, 10) } };
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

export default async function MapPage(): Promise<JSX.Element> {
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
      </section>

      <TonightMapShell
        initialEvents={envelope.data}
        recommendations={recommendations}
      />
    </>
  );
}
