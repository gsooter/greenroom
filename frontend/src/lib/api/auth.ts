/**
 * API client for Spotify OAuth start/complete endpoints.
 *
 * Called from client components only — the flow is inherently
 * interactive (browser redirect to Spotify and back) and cannot be
 * server-rendered.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, User } from "@/types";

export interface SpotifyStartResponse {
  authorize_url: string;
  state: string;
}

export interface SpotifyCompleteResponse {
  token: string;
  user: User;
}

export async function startSpotifyOAuth(): Promise<SpotifyStartResponse> {
  const res = await fetchJson<Envelope<SpotifyStartResponse>>(
    "/api/v1/auth/spotify/start",
    { revalidateSeconds: 0 },
  );
  return res.data;
}

export async function completeSpotifyOAuth(
  code: string,
  state: string,
): Promise<SpotifyCompleteResponse> {
  const res = await fetchJson<Envelope<SpotifyCompleteResponse>>(
    "/api/v1/auth/spotify/complete",
    {
      method: "POST",
      body: { code, state },
      revalidateSeconds: 0,
    },
  );
  return res.data;
}
