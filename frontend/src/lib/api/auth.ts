/**
 * API client for music-service connect endpoints (Spotify, Tidal,
 * Apple Music).
 *
 * Called from client components only — these flows are inherently
 * interactive (browser redirect or MusicKit JS prompt) and cannot be
 * server-rendered.
 *
 * TODO(phase5): the Apple Music helpers below are ready for the
 * MusicKit JS integration, but can only be exercised end-to-end once
 * the Apple Developer Program credentials land in the backend env.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, User } from "@/types";

export interface SpotifyStartResponse {
  authorize_url: string;
  state: string;
}

export interface SpotifyCompleteResponse {
  user: User;
}

export async function startSpotifyOAuth(
  token: string,
): Promise<SpotifyStartResponse> {
  const res = await fetchJson<Envelope<SpotifyStartResponse>>(
    "/api/v1/auth/spotify/start",
    { token, revalidateSeconds: 0 },
  );
  return res.data;
}

export async function completeSpotifyOAuth(
  token: string,
  code: string,
  state: string,
): Promise<SpotifyCompleteResponse> {
  const res = await fetchJson<Envelope<SpotifyCompleteResponse>>(
    "/api/v1/auth/spotify/complete",
    {
      method: "POST",
      body: { code, state },
      token,
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

export interface TidalStartResponse {
  authorize_url: string;
  state: string;
}

export interface TidalCompleteResponse {
  user: User;
}

export async function startTidalOAuth(
  token: string,
): Promise<TidalStartResponse> {
  const res = await fetchJson<Envelope<TidalStartResponse>>(
    "/api/v1/auth/tidal/start",
    { token, revalidateSeconds: 0 },
  );
  return res.data;
}

export async function completeTidalOAuth(
  token: string,
  code: string,
  state: string,
): Promise<TidalCompleteResponse> {
  const res = await fetchJson<Envelope<TidalCompleteResponse>>(
    "/api/v1/auth/tidal/complete",
    {
      method: "POST",
      body: { code, state },
      token,
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

export interface AppleMusicDeveloperTokenResponse {
  developer_token: string;
}

export interface AppleMusicConnectResponse {
  user: User;
}

/**
 * Mint a fresh MusicKit JS developer token. Only usable once the
 * Apple Developer Program credentials are populated server-side;
 * until then the backend returns a 503.
 */
export async function getAppleMusicDeveloperToken(
  token: string,
): Promise<AppleMusicDeveloperTokenResponse> {
  const res = await fetchJson<Envelope<AppleMusicDeveloperTokenResponse>>(
    "/api/v1/auth/apple-music/developer-token",
    { token, revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * POST the Music User Token MusicKit JS hands back to the browser
 * after the user consents on Apple's prompt.
 */
export async function connectAppleMusic(
  token: string,
  musicUserToken: string,
): Promise<AppleMusicConnectResponse> {
  const res = await fetchJson<Envelope<AppleMusicConnectResponse>>(
    "/api/v1/auth/apple-music/connect",
    {
      method: "POST",
      body: { music_user_token: musicUserToken },
      token,
      revalidateSeconds: 0,
    },
  );
  return res.data;
}
