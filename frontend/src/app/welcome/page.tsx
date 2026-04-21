/**
 * /welcome — four-step onboarding orchestrator.
 *
 * Reads the server-persisted onboarding state and advances the user
 * through any step they haven't finished. Each per-step component
 * handles its own persistence (genre patch, venue bulk-follow, music
 * connect, passkey register) and calls back with done/skip so this
 * page only owns the state-machine transitions.
 *
 * Task #72 adds the post-auth gate that redirects brand-new users
 * here automatically; for now /welcome is reachable directly.
 */

"use client";

import { useRouter } from "next/navigation";
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
import type { OnboardingState, OnboardingStepName } from "@/types";

const STEP_ORDER: readonly OnboardingStepName[] = [
  "taste",
  "venues",
  "music_services",
  "passkey",
];

export default function WelcomePage(): JSX.Element {
  const router = useRouter();
  const { user, token, isLoading, isAuthenticated, refreshUser } =
    useRequireAuth();

  const [state, setState] = useState<OnboardingState | null>(null);
  const [saving, setSaving] = useState<boolean>(false);

  useEffect(() => {
    if (!token) return;
    void getOnboardingState(token)
      .then(setState)
      .catch(() => setState(null));
  }, [token]);

  const currentStep = useMemo<OnboardingStepName | null>(() => {
    if (!state) return null;
    if (state.completed) return null;
    for (const step of STEP_ORDER) {
      if (!state.steps[step]) return step;
    }
    return null;
  }, [state]);

  useEffect(() => {
    if (state?.completed) {
      router.replace("/for-you");
    }
  }, [router, state]);

  const completeStep = useCallback(
    async (step: OnboardingStepName) => {
      if (!token) return;
      setSaving(true);
      try {
        const next = await markStepComplete(token, step);
        setState(next);
      } finally {
        setSaving(false);
      }
    },
    [token],
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
      <div className="mb-6">
        <WelcomeProgress
          steps={STEP_ORDER}
          current={currentStep}
          completedMap={state.steps}
        />
      </div>

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

      <div className="mt-10 border-t border-border pt-4 text-center">
        <button
          type="button"
          onClick={() => void skipAll()}
          disabled={saving}
          className="text-xs text-text-secondary underline underline-offset-2 disabled:opacity-60"
        >
          Skip the whole setup
        </button>
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
