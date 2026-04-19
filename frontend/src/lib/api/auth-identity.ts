/**
 * API client for Greenroom's identity endpoints.
 *
 * Identity (who you are) is separate from connected services like
 * Spotify (what we use to recommend for you). These endpoints cover
 * the four identity paths: magic-link email, Google OAuth, Apple
 * OAuth, and (soon) WebAuthn passkeys.
 *
 * Every function here is callable from client components only — the
 * flows are interactive and cannot be server-rendered.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, User } from "@/types";

export interface MagicLinkRequestResponse {
  email_sent: boolean;
}

export interface SessionResponse {
  token: string;
  user: User;
}

export interface OAuthStartResponse {
  authorize_url: string;
  state: string;
}

/**
 * Ask the backend to mail a magic link to the given address.
 *
 * The response is uninformative by design: it reports `email_sent: true`
 * regardless of whether the address exists or delivery succeeded.
 */
export async function requestMagicLink(
  email: string,
): Promise<MagicLinkRequestResponse> {
  const res = await fetchJson<Envelope<MagicLinkRequestResponse>>(
    "/api/v1/auth/magic-link/request",
    { method: "POST", body: { email }, revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Redeem a magic-link token and return a session JWT + serialized user.
 */
export async function verifyMagicLink(token: string): Promise<SessionResponse> {
  const res = await fetchJson<Envelope<SessionResponse>>(
    "/api/v1/auth/magic-link/verify",
    { method: "POST", body: { token }, revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Request a Google consent URL + signed state token.
 */
export async function startGoogleOAuth(): Promise<OAuthStartResponse> {
  const res = await fetchJson<Envelope<OAuthStartResponse>>(
    "/api/v1/auth/google/start",
    { revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Exchange a Google authorization code + state for a session JWT.
 */
export async function completeGoogleOAuth(
  code: string,
  state: string,
): Promise<SessionResponse> {
  const res = await fetchJson<Envelope<SessionResponse>>(
    "/api/v1/auth/google/complete",
    { method: "POST", body: { code, state }, revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Request an Apple consent URL + signed state token.
 */
export async function startAppleOAuth(): Promise<OAuthStartResponse> {
  const res = await fetchJson<Envelope<OAuthStartResponse>>(
    "/api/v1/auth/apple/start",
    { revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Exchange an Apple authorization code + state (and optional first-sign-in
 * user payload) for a session JWT.
 *
 * Apple only delivers the `user` blob on the first authorize. It carries
 * the display name since the id_token itself doesn't include it.
 */
export async function completeAppleOAuth(
  code: string,
  state: string,
  userData?: Record<string, unknown> | null,
): Promise<SessionResponse> {
  const res = await fetchJson<Envelope<SessionResponse>>(
    "/api/v1/auth/apple/complete",
    {
      method: "POST",
      body: { code, state, user: userData ?? null },
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

/**
 * Invalidate the server-side session (stateless JWT: a no-op for now).
 *
 * Always safe to call. The client should drop the stored token
 * immediately after this resolves.
 */
export async function logout(token: string): Promise<void> {
  await fetchJson<void>("/api/v1/auth/logout", {
    method: "POST",
    token,
    revalidateSeconds: 0,
  });
}
