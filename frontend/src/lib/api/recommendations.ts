/**
 * Typed API client for the authenticated recommendation endpoints.
 *
 * Backs the /for-you page. Every call here takes the session JWT from
 * AuthContext — the recommendation list is per-user and must never be
 * served from a shared SSR cache.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  Envelope,
  Paginated,
  Recommendation,
  SpotifyTopArtistsResponse,
} from "@/types";

export interface ListRecommendationsParams {
  page?: number;
  perPage?: number;
}

export async function listRecommendations(
  token: string,
  { page, perPage }: ListRecommendationsParams = {},
): Promise<Paginated<Recommendation>> {
  return fetchJson<Paginated<Recommendation>>("/api/v1/me/recommendations", {
    token,
    query: { page, per_page: perPage },
  });
}

export async function refreshRecommendations(
  token: string,
): Promise<{ generated: number }> {
  const res = await fetchJson<Envelope<{ generated: number }>>(
    "/api/v1/me/recommendations/refresh",
    { method: "POST", token },
  );
  return res.data;
}

export async function getMyTopArtists(
  token: string,
): Promise<SpotifyTopArtistsResponse> {
  const res = await fetchJson<Envelope<SpotifyTopArtistsResponse>>(
    "/api/v1/me/spotify/top-artists",
    { token },
  );
  return res.data;
}
