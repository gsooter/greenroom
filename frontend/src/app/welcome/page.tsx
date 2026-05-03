/**
 * /welcome — four-step onboarding orchestrator.
 *
 * Reads the server-persisted onboarding state and advances the user
 * through any step they haven't finished. Each per-step component
 * handles its own persistence (genre patch, venue bulk-follow, music
 * connect, passkey register) and calls back with done/skip so this
 * page only owns the state-machine transitions.
 *
 * Revisit mode (``?step=<name>``): completed users coming back from
 * settings or the home page's "Browse artists to follow" links land
 * here to add more taste signal. When ``?step=`` is present we render
 * that specific step regardless of ``state.completed`` and exit to the
 * URL in ``?return=`` (defaulting to /settings) on done/skip — the
 * normal onboarding flow leaves both query params unset and keeps its
 * existing redirect-to-/for-you behavior.
 */

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { MusicServicesStep } from "@/components/welcome/MusicServicesStep";
import { PasskeyStep } from "@/components/welcome/PasskeyStep";
import { TasteStep } from "@/components/welcome/TasteStep";
import { VenuesStep } from "@/components/welcome/VenuesStep";
import { WelcomeProgress } from "@/components/welcome/WelcomeProgress";
import {
  getOnboardingState,
  markStepComplete,
  skipOnboardingEntirely,
} from "@/lib/api/onboarding";
import { useRequireAuth } from "@/lib/auth";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "@/lib/config";
import type { OnboardingState, OnboardingStepName } from "@/types";

const STEP_ORDER: readonly OnboardingStepName[] = [
  "taste",
  "venues",
  "music_services",
  "passkey",
];

const DEFAULT_REVISIT_RETURN = "/settings";

/**
 * Coerce a raw ``?step=`` value into a valid step name.
 *
 * Returns null for missing or unknown values so the page can fall
 * through to its server-state-driven flow.
 */
function parseRequestedStep(raw: string | null): OnboardingStepName | null {
  if (!raw) return null;
  return STEP_ORDER.includes(raw as OnboardingStepName)
    ? (raw as OnboardingStepName)
    : null;
}

/**
 * Sanitize the ``?return=`` value to avoid open-redirect issues.
 *
 * Only same-origin paths starting with ``/`` are accepted, mirroring
 * the convention the auth callbacks use for post-login routing.
 */
function parseReturnUrl(raw: string | null): string {
  if (!raw) return DEFAULT_REVISIT_RETURN;
  if (!raw.startsWith("/") || raw.startsWith("//")) return DEFAULT_REVISIT_RETURN;
  return raw;
}

export default function WelcomePage(): JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, token, isLoading, isAuthenticated, refreshUser } =
    useRequireAuth();

  const [state, setState] = useState<OnboardingState | null>(null);
  const [saving, setSaving] = useState<boolean>(false);

  const requestedStep = parseRequestedStep(searchParams.get("step"));
  const returnUrl = parseReturnUrl(searchParams.get("return"));
  const isRevisit = requestedStep !== null;

  useEffect(() => {
    if (!token) return;
    void getOnboardingState(token)
      .then(setState)
      .catch(() => setState(null));
  }, [token]);

  const currentStep = useMemo<OnboardingStepName | null>(() => {
    if (requestedStep) return requestedStep;
    if (!state) return null;
    if (state.completed) return null;
    for (const step of STEP_ORDER) {
      if (!state.steps[step]) return step;
    }
    return null;
  }, [requestedStep, state]);

  useEffect(() => {
    // Revisit mode never auto-redirects — the user is here on purpose
    // even though their onboarding row says completed.
    if (isRevisit) return;
    if (state?.completed) {
      router.replace("/for-you");
    }
  }, [isRevisit, router, state]);

  const completeStep = useCallback(
    async (step: OnboardingStepName) => {
      if (!token) return;
      setSaving(true);
      try {
        const next = await markStepComplete(token, step);
        setState(next);
        if (isRevisit) {
          router.replace(returnUrl);
        }
      } finally {
        setSaving(false);
      }
    },
    [isRevisit, returnUrl, router, token],
  );

  const skipAll = useCallback(async () => {
    if (!token) return;
    setSaving(true);
    try {
      const next = await skipOnboardingEntirely(token);
      setState(next);
      router.replace("/events");
    } finally {
      setSaving(false);
    }
  }, [router, token]);

  if (isLoading || !isAuthenticated || !user || !state) {
    return <Shell>Loading…</Shell>;
  }

  if (!currentStep) {
    return <Shell>All set — redirecting…</Shell>;
  }

  return (
    <Shell>
      {!isRevisit ? (
        <div className="mb-6">
          <WelcomeProgress
            steps={STEP_ORDER}
            current={currentStep}
            completedMap={state.steps}
          />
        </div>
      ) : null}

      {currentStep === "taste" ? (
        <TasteStep
          token={token!}
          user={user}
          onDone={() => void completeStep("taste")}
          onSkip={() => void completeStep("taste")}
          onRefreshUser={refreshUser}
        />
      ) : null}

      {currentStep === "venues" ? (
        <VenuesStep
          token={token!}
          onDone={() => void completeStep("venues")}
          onSkip={() => void completeStep("venues")}
        />
      ) : null}

      {currentStep === "music_services" ? (
        <MusicServicesStep
          token={token!}
          onDone={() => void completeStep("music_services")}
          onSkip={() => void completeStep("music_services")}
        />
      ) : null}

      {currentStep === "passkey" ? (
        <PasskeyStep
          token={token!}
          onDone={() => void completeStep("passkey")}
          onSkip={() => void completeStep("passkey")}
        />
      ) : null}

      <div className="mt-10 space-y-2 border-t border-border pt-4 text-center">
        {isRevisit ? (
          <button
            type="button"
            onClick={() => router.replace(returnUrl)}
            disabled={saving}
            className="text-xs text-text-secondary underline underline-offset-2 disabled:opacity-60"
          >
            Done — back to settings
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void skipAll()}
            disabled={saving}
            className="text-xs text-text-secondary underline underline-offset-2 disabled:opacity-60"
          >
            Skip the whole setup
          </button>
        )}
        <p className="text-xs text-text-secondary">
          Stuck? We&apos;re at{" "}
          <a
            href={SUPPORT_MAILTO}
            className="text-text-primary underline underline-offset-2"
          >
            {SUPPORT_EMAIL}
          </a>
          .
        </p>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <div className="rounded-2xl border border-border bg-bg-surface p-8">
        {children}
      </div>
    </main>
  );
}
