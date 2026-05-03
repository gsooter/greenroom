/**
 * Segmented pill toggle for the home page card density preference.
 *
 * Renders two pressable links — Comfortable (default vertical hero
 * layout) and Compact (single-row list). Clicking one writes the
 * preference via :func:`useCompactMode` so every consumer in the same
 * tab re-renders to match.
 *
 * Lives at the top of the personalized area so signed-in users see
 * it inline with their content. Anonymous visitors don't see the
 * toggle, but the BrowseCardGrid still respects whatever value lives
 * in localStorage from a prior signed-in session.
 */

"use client";

import { useCompactMode } from "@/lib/home-preferences";

export default function CompactModeToggle(): JSX.Element {
  const [compact, setCompact] = useCompactMode();

  return (
    <div
      role="group"
      aria-label="Card density"
      className="inline-flex overflow-hidden rounded-full border border-border bg-bg-white text-xs font-medium"
      data-testid="home-compact-toggle"
    >
      <button
        type="button"
        aria-pressed={!compact}
        onClick={() => setCompact(false)}
        className={
          "px-3 py-1 transition " +
          (!compact
            ? "bg-green-primary text-text-inverse"
            : "text-text-secondary hover:text-foreground")
        }
      >
        Comfortable
      </button>
      <button
        type="button"
        aria-pressed={compact}
        onClick={() => setCompact(true)}
        className={
          "px-3 py-1 transition " +
          (compact
            ? "bg-green-primary text-text-inverse"
            : "text-text-secondary hover:text-foreground")
        }
      >
        Compact
      </button>
    </div>
  );
}
