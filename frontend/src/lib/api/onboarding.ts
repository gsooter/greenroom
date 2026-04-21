/**
 * API client for onboarding state and genre catalog endpoints.
 *
 * State mutations go through the authenticated /me/onboarding/* routes.
 * The genre catalog is public — server components can import and call
 * listGenres without a token.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  Envelope,
  Genre,
  OnboardingState,
  OnboardingStepName,
} from "@/types";

export async function getOnboardingState(
  token: string,
): Promise<OnboardingState> {
  const res = await fetchJson<Envelope<OnboardingState>>(
    "/api/v1/me/onboarding",
    { token },
  );
  return res.data;
}

export async function markStepComplete(
  token: string,
  step: OnboardingStepName,
): Promise<OnboardingState> {
  const res = await fetchJson<Envelope<OnboardingState>>(
    `/api/v1/me/onboarding/steps/${encodeURIComponent(step)}/complete`,
    { method: "POST", token },
  );
  return res.data;
}

export async function skipOnboardingEntirely(
  token: string,
): Promise<OnboardingState> {
  const res = await fetchJson<Envelope<OnboardingState>>(
    "/api/v1/me/onboarding/skip-all",
    { method: "POST", token },
  );
  return res.data;
}

export async function dismissOnboardingBanner(
  token: string,
): Promise<OnboardingState> {
  const res = await fetchJson<Envelope<OnboardingState>>(
    "/api/v1/me/onboarding/banner/dismiss",
    { method: "POST", token },
  );
  return res.data;
}

export async function incrementBrowseSessions(
  token: string,
): Promise<OnboardingState> {
  const res = await fetchJson<Envelope<OnboardingState>>(
    "/api/v1/me/onboarding/sessions/increment",
    { method: "POST", token },
  );
  return res.data;
}

export async function listGenres(
  revalidateSeconds: number = 60 * 60,
): Promise<Genre[]> {
  const res = await fetchJson<Envelope<{ genres: Genre[] }>>(
    "/api/v1/genres",
    { revalidateSeconds },
  );
  return res.data.genres;
}
