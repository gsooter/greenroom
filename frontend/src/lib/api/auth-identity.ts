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
import type {
  AuthenticationCredentialJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationCredentialJSON,
} from "@/lib/webauthn";
import type { Envelope, User } from "@/types";

export interface MagicLinkRequestResponse {
  email_sent: boolean;
}

export interface SessionResponse {
  token: string;
  token_expires_at: string | null;
  refresh_token: string | null;
  refresh_token_expires_at: string | null;
  user: User;
}

export interface OAuthStartResponse {
  authorize_url: string;
  state: string;
}

export interface PasskeyRegistrationChallenge {
  options: PublicKeyCredentialCreationOptionsJSON;
  state: string;
}

export interface PasskeyAuthenticationChallenge {
  options: PublicKeyCredentialRequestOptionsJSON;
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
 * Begin a WebAuthn registration ceremony for the signed-in user.
 *
 * Returns creation options (challenge, rp, user, excludeCredentials)
 * plus a short-lived signed state token. The state must be posted back
 * with the attestation so the backend can recover the challenge.
 */
export async function startPasskeyRegistration(
  token: string,
): Promise<PasskeyRegistrationChallenge> {
  const res = await fetchJson<Envelope<PasskeyRegistrationChallenge>>(
    "/api/v1/auth/passkey/register/start",
    { method: "POST", token, revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Verify an attestation and persist the new passkey credential under
 * the signed-in user.
 */
export async function completePasskeyRegistration(
  token: string,
  credential: RegistrationCredentialJSON,
  state: string,
  name?: string,
): Promise<{ registered: boolean }> {
  const res = await fetchJson<Envelope<{ registered: boolean }>>(
    "/api/v1/auth/passkey/register/complete",
    {
      method: "POST",
      token,
      body: { credential, state, name: name ?? null },
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

/**
 * Begin a WebAuthn authentication ceremony for an anonymous visitor.
 *
 * Returns request options with an empty ``allowCredentials`` list so
 * the platform surfaces any discoverable credential bound to the
 * relying-party id.
 */
export async function startPasskeyAuthentication(): Promise<PasskeyAuthenticationChallenge> {
  const res = await fetchJson<Envelope<PasskeyAuthenticationChallenge>>(
    "/api/v1/auth/passkey/authenticate/start",
    { method: "POST", revalidateSeconds: 0 },
  );
  return res.data;
}

/**
 * Verify a passkey assertion and mint a Greenroom session JWT.
 */
export async function completePasskeyAuthentication(
  credential: AuthenticationCredentialJSON,
  state: string,
): Promise<SessionResponse> {
  const res = await fetchJson<Envelope<SessionResponse>>(
    "/api/v1/auth/passkey/authenticate/complete",
    {
      method: "POST",
      body: { credential, state },
      revalidateSeconds: 0,
    },
  );
  return res.data;
}

/**
 * Rotate a refresh token into a fresh access+refresh pair.
 *
 * Knuckles single-uses refresh tokens — the returned
 * `refresh_token` replaces the one passed in. Callers must store
 * both new values before discarding the old pair.
 */
export async function refreshSession(
  refreshToken: string,
): Promise<SessionResponse> {
  const res = await fetchJson<Envelope<SessionResponse>>(
    "/api/v1/auth/refresh",
    {
      method: "POST",
      body: { refresh_token: refreshToken },
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
