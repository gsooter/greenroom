/**
 * Shared intro blurb for each step in the /welcome flow.
 *
 * Each step headline tells the user *what* they are about to do. The
 * intro explains *why* it's worth their time — in a warm, short tone
 * that matches the rest of the onboarding copy.
 */

import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
}

/**
 * Renders a soft-accent callout below the step header.
 *
 * @param children - The copy to display — keep it under ~35 words.
 * @returns The styled intro block.
 */
export function StepIntro({ children }: Props): JSX.Element {
  return (
    <div className="rounded-lg border border-blush-soft/60 bg-blush-soft/40 px-4 py-3 text-sm leading-relaxed text-text-primary">
      {children}
    </div>
  );
}
