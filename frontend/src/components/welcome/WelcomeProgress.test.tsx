/**
 * Tests for WelcomeProgress.
 *
 * Chip styling encodes three states — done (green fill), current
 * (green outline), upcoming (neutral). These tests lock in that each
 * state renders the right glyph (✓ vs number) so a future style tweak
 * can't silently flip which step looks like the active one.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WelcomeProgress } from "@/components/welcome/WelcomeProgress";
import type { OnboardingStepName } from "@/types";

const STEPS: readonly OnboardingStepName[] = [
  "taste",
  "venues",
  "music_services",
  "passkey",
];

function completedMap(
  done: OnboardingStepName[],
): Record<OnboardingStepName, boolean> {
  return {
    taste: done.includes("taste"),
    venues: done.includes("venues"),
    music_services: done.includes("music_services"),
    passkey: done.includes("passkey"),
  };
}

describe("WelcomeProgress", () => {
  it("renders checkmarks for completed steps and numbers for the rest", () => {
    render(
      <WelcomeProgress
        steps={STEPS}
        current="music_services"
        completedMap={completedMap(["taste", "venues"])}
      />,
    );

    // Completed: two ✓ glyphs (taste + venues).
    expect(screen.getAllByText("✓")).toHaveLength(2);
    // Current and upcoming show their 1-indexed position.
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("marks the current step with the outlined green chip", () => {
    render(
      <WelcomeProgress
        steps={STEPS}
        current="taste"
        completedMap={completedMap([])}
      />,
    );
    const firstChip = screen.getByText("1");
    // Outlined = text-green-primary (fill reserved for done state).
    expect(firstChip.className).toContain("text-green-primary");
  });
});
