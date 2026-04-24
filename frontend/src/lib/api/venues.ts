/**
 * Typed API client functions for venues.
 *
 * Wraps `GET /api/v1/venues` and `GET /api/v1/venues/:slug`. The list
 * endpoint requires either `region` or `cityId` — the backend returns
 * 422 if neither is supplied, so callers must pass one.
 */

import { ApiRequestError, fetchJson } from "@/lib/api/client";
import type {
  Paginated,
  Envelope,
  Region,
  VenueDetail,
  VenueSummary,
} from "@/types";

export interface ListVenuesParams {
  region?: Region;
  cityId?: string;
  activeOnly?: boolean;
  page?: number;
  perPage?: number;
  revalidateSeconds?: number;
}

export async function listVenues(
  params: ListVenuesParams,
): Promise<Paginated<VenueSummary>> {
  const {
    region,
    cityId,
    activeOnly,
    page,
    perPage,
    revalidateSeconds,
  } = params;

  return fetchJson<Paginated<VenueSummary>>("/api/v1/venues", {
    query: {
      region,
      city_id: cityId,
      active_only: activeOnly,
      page,
      per_page: perPage,
    },
    revalidateSeconds,
  });
}

export async function getVenueBySlug(
  slug: string,
  revalidateSeconds?: number,
): Promise<VenueDetail> {
  const res = await fetchJson<Envelope<VenueDetail>>(
    `/api/v1/venues/${encodeURIComponent(slug)}`,
    { revalidateSeconds },
  );
  return res.data;
}

export interface VenueMapSnapshot {
  url: string;
  width: number;
  height: number;
}

export interface GetVenueMapSnapshotParams {
  slug: string;
  width?: number;
  height?: number;
  scheme?: "light" | "dark";
  revalidateSeconds?: number;
}

/**
 * Fetch a signed Apple Maps snapshot URL for a venue.
 *
 * Returns ``null`` when the backend replies with 404 or 503 (no
 * coordinates, or the environment lacks Apple Maps credentials) so the
 * caller can render a snapshot-free layout without throwing.
 *
 * @param params - Slug plus optional image dimensions and color scheme.
 * @returns Snapshot envelope or ``null`` if the snapshot is unavailable.
 */
export async function getVenueMapSnapshot(
  params: GetVenueMapSnapshotParams,
): Promise<VenueMapSnapshot | null> {
  const { slug, width, height, scheme, revalidateSeconds = 86400 } = params;
  try {
    const res = await fetchJson<Envelope<VenueMapSnapshot>>(
      `/api/v1/venues/${encodeURIComponent(slug)}/map-snapshot`,
      {
        query: { width, height, scheme },
        revalidateSeconds,
      },
    );
    return res.data;
  } catch (err) {
    if (err instanceof ApiRequestError) return null;
    throw err;
  }
}

export interface NearbyPoi {
  name: string;
  category: string;
  address: string | null;
  latitude: number;
  longitude: number;
  distance_m: number;
}

export interface GetVenueNearbyPoisParams {
  slug: string;
  limit?: number;
  categories?: readonly string[];
  revalidateSeconds?: number;
}

/**
 * Fetch the Apple-backed nearby-POI list for a venue.
 *
 * Fails soft: returns an empty list when the backend is unavailable so
 * the caller can render the page without a broken section.
 *
 * @param params - Slug, limit, and optional Apple POI categories.
 * @returns Array of nearby POIs (possibly empty).
 */
export async function getVenueNearbyPois(
  params: GetVenueNearbyPoisParams,
): Promise<NearbyPoi[]> {
  const { slug, limit, categories, revalidateSeconds = 86400 } = params;
  try {
    const res = await fetchJson<{ data: NearbyPoi[]; meta: { count: number } }>(
      `/api/v1/venues/${encodeURIComponent(slug)}/nearby`,
      {
        query: {
          limit,
          categories:
            categories && categories.length ? categories.join(",") : undefined,
        },
        revalidateSeconds,
      },
    );
    return res.data;
  } catch (err) {
    if (err instanceof ApiRequestError) return [];
    throw err;
  }
}
