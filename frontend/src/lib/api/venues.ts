/**
 * Typed API client functions for venues.
 *
 * Wraps `GET /api/v1/venues` and `GET /api/v1/venues/:slug`. The list
 * endpoint requires either `region` or `cityId` — the backend returns
 * 422 if neither is supplied, so callers must pass one.
 */

import { fetchJson } from "@/lib/api/client";
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
