/**
 * Frosted-glass heart button that floats over a card image.
 *
 * Pure presentational primitive: it owns the markup and the heart
 * glyph, but knows nothing about auth, persistence, or which thing is
 * being saved. Callers (e.g. SaveEventButton) wire `saved`, `onClick`,
 * and the right `aria-label` for their domain.
 */

"use client";

import type { MouseEvent } from "react";

interface FloatingHeartButtonProps {
  /** True when the underlying item is saved by the current user. */
  saved: boolean;
  /** Click handler. Receives the original mouse event. */
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  /** Required for screen readers — describes the action, not the icon. */
  ariaLabel: string;
  /** Disable while auth or save state is in flight. */
  disabled?: boolean;
}

/**
 * Renders the floating heart button. Caller controls the saved state
 * and click handler; this component owns the visual chrome.
 */
export default function FloatingHeartButton({
  saved,
  onClick,
  ariaLabel,
  disabled = false,
}: FloatingHeartButtonProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-pressed={saved}
      data-saved={saved ? "true" : "false"}
      className={
        "inline-flex h-9 w-9 items-center justify-center rounded-full border shadow-sm ring-1 ring-black/5 backdrop-blur transition disabled:opacity-50 " +
        (saved
          ? "border-blush-accent bg-blush-soft text-blush-accent"
          : "border-border bg-bg-white/90 text-text-primary hover:border-blush-accent hover:text-blush-accent")
      }
    >
      <HeartIcon filled={saved} />
    </button>
  );
}

/**
 * Heart glyph. Stroke width 2 in both states; filled state also sets
 * fill to currentColor so the saved blush color paints both the
 * outline and the body.
 */
function HeartIcon({ filled }: { filled: boolean }): JSX.Element {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={18}
      height={18}
      aria-hidden
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
    </svg>
  );
}
