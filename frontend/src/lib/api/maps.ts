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
