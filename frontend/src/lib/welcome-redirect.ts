/**
 * Post-auth redirect helper.
 *
 * Every sign-in path (magic link, Google, Apple, passkey) calls this
 * after persisting the new session. If the user hasn't finished the
 * four-step /welcome flow, we route them there; otherwise we fall
 * back to ``/for-you`` (or whatever the caller supplied).
 *
 * A separate sessionStorage flag — ``greenroom.welcome_return`` — lets
 * the music-services step interrupt a Spotify/Tidal OAuth redirect and
 * come back to /welcome instead of /settings. The flag is consumed
 * (cleared) once handled so a later natural sign-in doesn't trap the
 * user back in the flow.
 */

import { getOnboardingState } from "@/lib/api/onboarding";

const WELCOME_RETURN_KEY = "greenroom.welcome_return";

export function consumeWelcomeReturnFlag(): string | null {
  if (typeof window === "undefined") return null;
  const value = window.sessionStorage.getItem(WELCOME_RETURN_KEY);
  if (value) window.sessionStorage.removeItem(WELCOME_RETURN_KEY);
  return value;
}

export async function resolvePostAuthDestination(
  token: string,
  fallback: string = "/for-you",
): Promise<string> {
  try {
    const state = await getOnboardingState(token);
    if (!state.completed) return "/welcome";
  } catch {
    /* If the state call fails, fall through — better to land the user
       somewhere than bounce them on a loading page. */
  }
  return fallback;
}
