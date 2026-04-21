/**
 * API client for artist search + follow-graph endpoints.
 *
 * Mirrors :mod:`backend.api.v1.onboarding` for everything under
 * ``/artists``, ``/me/followed-artists``, and ``/me/followed-venues``.
 */

import { fetchJson } from "@/lib/api/client";
import type { ArtistSummary, Envelope, Paginated, VenueSummary } from "@/types";

export async function searchArtists(
  token: string,
  query: string,
  limit: number = 10,
): Promise<ArtistSummary[]> {
  const res = await fetchJson<Envelope<{ artists: ArtistSummary[] }>>(
    "/api/v1/artists",
    { token, query: { query, limit } },
  );
  return res.data.artists;
}

export async function followArtist(
  token: string,
  artistId: string,
): Promise<void> {
  await fetchJson<Envelope<{ followed: boolean }>>(
    `/api/v1/me/followed-artists/${encodeURIComponent(artistId)}`,
    { method: "POST", token },
  );
}

export async function unfollowArtist(
  token: string,
  artistId: string,
): Promise<void> {
  await fetchJson<void>(
    `/api/v1/me/followed-artists/${encodeURIComponent(artistId)}`,
    { method: "DELETE", token },
  );
}

export async function listFollowedArtists(
  token: string,
  page: number = 1,
  perPage: number = 50,
): Promise<Paginated<ArtistSummary>> {
  return fetchJson<Paginated<ArtistSummary>>("/api/v1/me/followed-artists", {
    token,
    query: { page, per_page: perPage },
  });
}

export async function followVenuesBulk(
  token: string,
  venueIds: string[],
): Promise<number> {
  const res = await fetchJson<Envelope<{ written: number }>>(
    "/api/v1/me/followed-venues",
    { method: "POST", token, body: { venue_ids: venueIds } },
  );
  return res.data.written;
}

export async function unfollowVenue(
  token: string,
  venueId: string,
): Promise<void> {
  await fetchJson<void>(
    `/api/v1/me/followed-venues/${encodeURIComponent(venueId)}`,
    { method: "DELETE", token },
  );
}

export async function listFollowedVenues(
  token: string,
  page: number = 1,
  perPage: number = 50,
): Promise<Paginated<VenueSummary>> {
  return fetchJson<Paginated<VenueSummary>>("/api/v1/me/followed-venues", {
    token,
    query: { page, per_page: perPage },
  });
}
