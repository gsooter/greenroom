/**
 * Typed API client functions for the map-discovery surfaces.
 *
 * Wraps three backend endpoints:
 *
 * * ``GET /api/v1/maps/tonight`` — tonight's DMV pins for the Tonight
 *   map. See :func:`backend.services.events.list_tonight_map_events`.
 * * ``GET /api/v1/maps/recommendations`` — community pins overlay.
 * * ``GET /api/v1/maps/token`` — short-lived MapKit JS developer
 *   token, minted per origin.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  MapRecommendation,
  NearMeEnvelope,
  NearMeWindow,
  TonightMapEnvelope,
} from "@/types";

export interface GetTonightMapParams {
  genres?: string[];
  revalidateSeconds?: number;
}

export async function getTonightMap(
  params: GetTonightMapParams = {},
): Promise<TonightMapEnvelope> {
  const { genres, revalidateSeconds } = params;
  return fetchJson<TonightMapEnvelope>("/api/v1/maps/tonight", {
    query: {
      genres: genres && genres.length > 0 ? genres.join(",") : undefined,
    },
    revalidateSeconds,
  });
}

export interface GetMapRecommendationsParams {
  swLat: number;
  swLng: number;
  neLat: number;
  neLng: number;
  category?: string;
  sort?: "top" | "new";
  limit?: number;
  sessionId?: string;
  token?: string | null;
  revalidateSeconds?: number;
}

interface MapRecommendationsEnvelope {
  data: MapRecommendation[];
  meta: { count: number };
}

export async function getMapRecommendations(
  params: GetMapRecommendationsParams,
): Promise<MapRecommendation[]> {
  const {
    swLat,
    swLng,
    neLat,
    neLng,
    category,
    sort,
    limit,
    sessionId,
    token,
    revalidateSeconds,
  } = params;

  const res = await fetchJson<MapRecommendationsEnvelope>(
    "/api/v1/maps/recommendations",
    {
      query: {
        sw_lat: swLat,
        sw_lng: swLng,
        ne_lat: neLat,
        ne_lng: neLng,
        category,
        sort,
        limit,
        session_id: sessionId,
      },
      token,
      revalidateSeconds,
    },
  );
  return res.data;
}

export interface GetNearMeEventsParams {
  latitude: number;
  longitude: number;
  radiusKm?: number;
  window?: NearMeWindow;
  limit?: number;
  revalidateSeconds?: number;
}

/**
 * Fetch upcoming DMV events within a radius of a lat/lng, nearest first.
 * Wraps `GET /api/v1/maps/near-me`.
 */
export async function getNearMeEvents(
  params: GetNearMeEventsParams,
): Promise<NearMeEnvelope> {
  const { latitude, longitude, radiusKm, window, limit, revalidateSeconds } =
    params;
  return fetchJson<NearMeEnvelope>("/api/v1/maps/near-me", {
    query: {
      lat: latitude,
      lng: longitude,
      radius_km: radiusKm,
      window,
      limit,
    },
    revalidateSeconds,
  });
}

export interface SubmitMapRecommendationInput {
  query: string;
  by: "name" | "address";
  lat?: number;
  lng?: number;
  venueId?: string;
  category: string;
  body: string;
  honeypot?: string;
  sessionId?: string;
}

/**
 * POST a new community recommendation to
 * `/api/v1/maps/recommendations`. When `venueId` is supplied the backend
 * uses the venue's own coords as the anchor and enforces the 1000 m
 * guardrail — the caller does not need to supply `lat`/`lng`.
 */
export async function submitMapRecommendation(
  input: SubmitMapRecommendationInput,
  token: string | null,
): Promise<MapRecommendation> {
  const { sessionId, venueId, ...rest } = input;
  const res = await fetchJson<{ data: MapRecommendation }>(
    "/api/v1/maps/recommendations",
    {
      method: "POST",
      token: token ?? undefined,
      body: {
        query: rest.query,
        by: rest.by,
        lat: rest.lat,
        lng: rest.lng,
        venue_id: venueId,
        category: rest.category,
        body: rest.body,
        honeypot: rest.honeypot ?? "",
        // Backend ignores session_id when the caller is signed in.
        session_id: token ? undefined : sessionId,
      },
    },
  );
  return res.data;
}

export interface VoteOnMapRecommendationResult {
  likes: number;
  dislikes: number;
  viewer_vote: number | null;
  suppressed: boolean;
}

/**
 * POST a vote on a community recommendation. `value` must be -1, 0, or
 * +1 — 0 clears an existing vote.
 */
export async function voteOnMapRecommendation(
  recommendationId: string,
  token: string | null,
  value: -1 | 0 | 1,
  sessionId: string | null,
): Promise<VoteOnMapRecommendationResult> {
  const res = await fetchJson<{ data: VoteOnMapRecommendationResult }>(
    `/api/v1/maps/recommendations/${recommendationId}/vote`,
    {
      method: "POST",
      token: token ?? undefined,
      body: {
        value,
        session_id: token ? undefined : sessionId,
      },
    },
  );
  return res.data;
}

export interface NearbyPlace {
  name: string;
  category: string | null;
  address: string | null;
  latitude: number;
  longitude: number;
  distance_m: number;
}

export interface SearchPlacesParams {
  latitude: number;
  longitude: number;
  q?: string;
  categories?: string[];
  radiusM?: number;
  limit?: number;
}

/**
 * Authed autocomplete for the "Leave a tip" place picker. Hits
 * `/api/v1/maps/places/search`; the backend enforces a per-user
 * rate limit of 20 req/min.
 */
export async function searchNearbyPlaces(
  params: SearchPlacesParams,
  token: string,
): Promise<NearbyPlace[]> {
  const { latitude, longitude, q, categories, radiusM, limit } = params;
  const res = await fetchJson<{ data: NearbyPlace[]; meta: { count: number } }>(
    "/api/v1/maps/places/search",
    {
      token,
      query: {
        lat: latitude,
        lng: longitude,
        q,
        categories: categories && categories.length ? categories.join(",") : undefined,
        radius_m: radiusM,
        limit,
      },
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

export interface ListVenueTipsParams {
  category?: string;
  limit?: number;
  sessionId?: string;
}

/**
 * GET recommendations anchored to a venue. Returns rows with
 * `distance_from_venue_m` populated so the UI can render "120 m from
 * Black Cat". Public — auth optional, only affects viewer_vote.
 */
export async function listVenueTips(
  slug: string,
  token: string | null,
  params: ListVenueTipsParams = {},
): Promise<MapRecommendation[]> {
  const { category, limit, sessionId } = params;
  const res = await fetchJson<{
    data: MapRecommendation[];
    meta: { count: number };
  }>(`/api/v1/venues/${slug}/tips`, {
    token: token ?? undefined,
    query: {
      category,
      limit,
      session_id: token ? undefined : sessionId,
    },
    revalidateSeconds: 0,
  });
  return res.data;
}

export interface MapKitToken {
  token: string;
  expires_at: number;
}

interface MapKitTokenEnvelope {
  data: MapKitToken;
}

export interface GetMapKitTokenParams {
  origin?: string | null;
  revalidateSeconds?: number;
}

export async function getMapKitToken(
  params: GetMapKitTokenParams = {},
): Promise<MapKitToken> {
  const { origin, revalidateSeconds } = params;
  const res = await fetchJson<MapKitTokenEnvelope>("/api/v1/maps/token", {
    query: { origin: origin ?? undefined },
    revalidateSeconds,
  });
  return res.data;
}
