/**
 * Progress indicator for the four-step /welcome flow.
 *
 * Renders a small numbered chip row so the user always knows where
 * they are (and what's left) without having to count.
 */

"use client";

import type { OnboardingStepName } from "@/types";

interface Props {
  steps: readonly OnboardingStepName[];
  current: OnboardingStepName;
  completedMap: Record<OnboardingStepName, boolean>;
}

const LABEL: Record<OnboardingStepName, string> = {
  taste: "Taste",
  venues: "Venues",
  music_services: "Music",
  passkey: "Passkey",
};

export function WelcomeProgress({
  steps,
  current,
  completedMap,
}: Props): JSX.Element {
  const currentIndex = steps.indexOf(current);
  return (
    <ol className="flex items-center justify-center gap-2 text-xs font-medium">
      {steps.map((step, idx) => {
        const isCurrent = step === current;
        const isDone = completedMap[step];
        const isUpcoming = idx > currentIndex && !isDone;

        const baseChip =
          "flex h-7 w-7 items-center justify-center rounded-full border text-[11px]";
        const chipClass = isDone
          ? `${baseChip} border-green-primary bg-green-primary text-text-inverse`
          : isCurrent
            ? `${baseChip} border-green-primary bg-bg-white text-green-primary`
            : `${baseChip} border-border bg-bg-white text-text-secondary`;

        const labelClass = isUpcoming
          ? "text-text-secondary"
          : "text-text-primary";

        return (
          <li key={step} className="flex items-center gap-2">
            <span className={chipClass} aria-hidden>
              {isDone ? "✓" : idx + 1}
            </span>
            <span className={`hidden sm:inline ${labelClass}`}>
              {LABEL[step]}
            </span>
            {idx < steps.length - 1 ? (
              <span className="h-px w-6 bg-border" aria-hidden />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
