/**
 * Typed API client functions for cities.
 *
 * Wraps `GET /api/v1/cities` and `GET /api/v1/cities/:slug`. Used by
 * server components to render the city picker and city-scoped pages.
 */

import { fetchJson } from "@/lib/api/client";
import type { City, Envelope, Region } from "@/types";

export interface ListCitiesParams {
  region?: Region;
  revalidateSeconds?: number;
}

export async function listCities(
  params: ListCitiesParams = {},
): Promise<City[]> {
  const { region, revalidateSeconds } = params;
  const res = await fetchJson<Envelope<City[]>>("/api/v1/cities", {
    query: { region },
    revalidateSeconds,
  });
  return res.data;
}

export async function getCityBySlug(
  slug: string,
  revalidateSeconds?: number,
): Promise<City> {
  const res = await fetchJson<Envelope<City>>(
    `/api/v1/cities/${encodeURIComponent(slug)}`,
    { revalidateSeconds },
  );
  return res.data;
}
