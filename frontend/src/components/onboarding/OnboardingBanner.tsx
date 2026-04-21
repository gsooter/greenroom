/**
 * Persistent onboarding nudge banner.
 *
 * Shown on authenticated browse pages after a user bailed out of the
 * four-step ``/welcome`` flow. Lets them either resume setup or dismiss
 * the banner for good. Also responsible for bumping the "browse
 * sessions since skipped" counter once per browser session so the
 * banner auto-hides after seven browse visits.
 *
 * Renders nothing at all for signed-out users, users who finished
 * onboarding, or users whose banner has already been dismissed/aged
 * out — so it's safe to mount unconditionally from :class:`AppShell`.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  dismissOnboardingBanner,
  getOnboardingState,
  incrementBrowseSessions,
} from "@/lib/api/onboarding";
import { useAuth } from "@/lib/auth";
import type { OnboardingState } from "@/types";

const BUMP_KEY = "greenroom.browse_session_bumped";

export function OnboardingBanner(): JSX.Element | null {
  const { token, isAuthenticated, isLoading } = useAuth();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [hidden, setHidden] = useState<boolean>(false);

  useEffect(() => {
    if (isLoading || !isAuthenticated || !token) {
      setState(null);
      return;
    }
    void getOnboardingState(token)
      .then(setState)
      .catch(() => setState(null));
  }, [isAuthenticated, isLoading, token]);

  // Bump the counter once per browser session for skipped users so the
  // server-side seven-session auto-hide eventually fires.
  useEffect(() => {
    if (!token || !state?.skipped_entirely_at) return;
    if (typeof window === "undefined") return;
    if (window.sessionStorage.getItem(BUMP_KEY) === "1") return;
    window.sessionStorage.setItem(BUMP_KEY, "1");
    void incrementBrowseSessions(token)
      .then(setState)
      .catch(() => {
        /* best-effort — a failed bump just delays auto-hide */
      });
  }, [state?.skipped_entirely_at, token]);

  const handleDismiss = useCallback(async () => {
    if (!token) return;
    setHidden(true);
    try {
      await dismissOnboardingBanner(token);
    } catch {
      /* server will catch up on next mount */
    }
  }, [token]);

  if (!state?.banner.visible || hidden) return null;

  return (
    <div
      role="status"
      className="border-b border-blush-soft/60 bg-blush-soft/50"
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2 text-xs">
        <p className="text-text-primary">
          Finish setting up Greenroom so your picks actually know you.
        </p>
        <div className="flex items-center gap-3">
          <Link
            href="/welcome"
            className="rounded-md bg-green-primary px-3 py-1.5 text-[11px] font-medium text-text-inverse hover:bg-green-dark"
          >
            Finish setup
          </Link>
          <button
            type="button"
            onClick={() => void handleDismiss()}
            aria-label="Dismiss onboarding banner"
            className="rounded-md px-2 py-1 text-text-secondary hover:text-text-primary"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}
