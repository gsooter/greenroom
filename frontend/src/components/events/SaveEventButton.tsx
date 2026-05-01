/**
 * Save / unsave toggle button — client component.
 *
 * Reads the saved state from `SavedEventsContext` so every instance of
 * the button for the same event stays in sync via one shared source of
 * truth. Anonymous visitors get a toast prompt instead of a forced
 * redirect — clicking the heart logged-out preserves scroll position
 * and lets the user keep browsing.
 *
 * Two variants:
 *  - "icon"    — circular floating glass button rendered via the shared
 *                `FloatingHeartButton` primitive. Used over card images.
 *  - "pill"    — full-width pill for the event detail page CTA.
 */

"use client";

import { useCallback, type MouseEvent } from "react";

import FloatingHeartButton from "@/components/ui/FloatingHeartButton";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/lib/auth";
import { useSavedEvents } from "@/lib/saved-events-context";

interface SaveEventButtonProps {
  eventId: string;
  variant?: "icon" | "pill";
}

export default function SaveEventButton({
  eventId,
  variant = "icon",
}: SaveEventButtonProps): JSX.Element {
  const { show: showToast } = useToast();
  const { isAuthenticated, isLoading: authLoading } = useAuth();
  const { isSaved, toggle } = useSavedEvents();

  const saved = isAuthenticated && isSaved(eventId);

  const handleClick = useCallback(
    (event: MouseEvent<HTMLButtonElement>): void => {
      event.preventDefault();
      event.stopPropagation();
      if (!isAuthenticated) {
        showToast("Sign in to save shows.");
        return;
      }
      void toggle(eventId);
    },
    [isAuthenticated, showToast, toggle, eventId],
  );

  const label = saved ? "Remove from saved shows" : "Save this show";

  if (variant === "pill") {
    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={authLoading}
        aria-label={label}
        aria-pressed={saved}
        className={
          "inline-flex items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-semibold transition disabled:opacity-50 " +
          (saved
            ? "border-blush-accent bg-blush-soft text-blush-accent hover:bg-blush-soft/80"
            : "border-border bg-bg-surface text-text-primary hover:border-blush-accent hover:text-blush-accent")
        }
      >
        <PillHeartIcon filled={saved} />
        {saved ? "Saved" : "Save show"}
      </button>
    );
  }

  return (
    <FloatingHeartButton
      saved={saved}
      onClick={handleClick}
      ariaLabel={label}
      disabled={authLoading}
    />
  );
}

/**
 * Heart glyph for the inline pill variant. Kept local because the pill
 * variant doesn't need the `FloatingHeartButton` styling shell — only
 * a stroke/fill icon next to text.
 */
function PillHeartIcon({ filled }: { filled: boolean }): JSX.Element {
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
