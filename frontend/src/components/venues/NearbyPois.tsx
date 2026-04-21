/**
 * "Nearby" list for a venue — pulls POIs from the Apple Maps Server
 * API via the backend `/venues/:slug/nearby` endpoint.
 *
 * Server-rendered so the venue page's HTML already ships with the list,
 * which keeps this block scrapable by AI crawlers. Fails soft: if the
 * backend replies non-OK or the list is empty, the component renders
 * nothing.
 */

import { config } from "@/lib/config";

interface NearbyPoi {
  name: string;
  category: string;
  address: string | null;
  latitude: number;
  longitude: number;
  distance_m: number;
}

interface NearbyResponse {
  data: NearbyPoi[];
  meta: { count: number };
}

interface NearbyPoisProps {
  slug: string;
  venueName: string;
  limit?: number;
  categories?: readonly string[];
}

async function fetchNearby(
  slug: string,
  params: URLSearchParams,
): Promise<NearbyPoi[]> {
  const url = `${config.apiUrl}/api/v1/venues/${encodeURIComponent(slug)}/nearby?${params.toString()}`;
  const res = await fetch(url, { next: { revalidate: 86400 } });
  if (!res.ok) return [];
  const body = (await res.json()) as NearbyResponse;
  return body.data;
}

function formatDistance(meters: number): string {
  if (meters < 100) return `${meters} m`;
  if (meters < 1000) return `${Math.round(meters / 10) * 10} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

/**
 * Walking-distance list of bars, restaurants, and cafes near a venue.
 *
 * @param slug - Venue slug used to hit the backend nearby endpoint.
 * @param venueName - Display name of the venue, used in the heading.
 * @param limit - Max POIs to show. Defaults to 8.
 * @param categories - Apple POI categories. Defaults to the backend set
 *   (Restaurant, Bar, Cafe).
 */
export default async function NearbyPois({
  slug,
  venueName,
  limit = 8,
  categories,
}: NearbyPoisProps): Promise<JSX.Element | null> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (categories && categories.length > 0) {
    params.set("categories", categories.join(","));
  }

  const pois = await fetchNearby(slug, params);
  if (pois.length === 0) return null;

  return (
    <section
      aria-labelledby={`nearby-${slug}-heading`}
      className="flex flex-col gap-3"
    >
      <h2 id={`nearby-${slug}-heading`} className="text-xl font-semibold">
        Grab a bite before the show
      </h2>
      <p className="text-sm text-text-secondary">
        Bars, restaurants, and cafes within a short walk of {venueName}.
      </p>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {pois.map((poi) => (
          <li
            key={`${poi.name}-${poi.latitude}-${poi.longitude}`}
            className="flex items-start justify-between gap-3 rounded-md border border-border bg-bg-white px-3 py-2"
          >
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm font-medium text-text-primary">
                {poi.name}
              </span>
              <span className="truncate text-xs text-text-secondary">
                {poi.category}
                {poi.address ? ` · ${poi.address}` : ""}
              </span>
            </div>
            <span className="shrink-0 text-xs font-medium text-text-secondary">
              {formatDistance(poi.distance_m)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
