/**
 * Smart empty-state for /for-you.
 *
 * The page can be empty for two very different reasons, and the user
 * needs a different next step in each:
 *
 * 1. ``no_signal`` — the user hasn't connected a music service, hasn't
 *    picked any onboarding genres, and hasn't saved any shows. The
 *    scorers have nothing to compare against, so the engine returned
 *    zero rows by design. Send them to /settings to connect Spotify (or
 *    /events to start saving shows).
 * 2. ``no_matches`` — the user has signal but no upcoming events
 *    matched. Their taste is niche, or the calendar is sparse this week.
 *    Send them to /events to browse, and tell them to come back after
 *    the next nightly scrape.
 */

import Link from "next/link";

export type ForYouEmptyVariant = "no_signal" | "no_matches";

interface ForYouEmptyStateProps {
  variant: ForYouEmptyVariant;
}

export default function ForYouEmptyState({
  variant,
}: ForYouEmptyStateProps): JSX.Element {
  if (variant === "no_signal") {
    return (
      <div className="rounded-xl border border-border bg-bg-surface p-8 text-center">
        <h2 className="text-lg font-semibold text-text-primary">
          Connect a music service to see picks
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          Greenroom uses your listening history to surface DMV shows you&apos;ll
          actually want to go to. Connect Spotify in{" "}
          <Link
            href="/settings"
            className="text-text-primary underline underline-offset-2"
          >
            settings
          </Link>{" "}
          — or save a few shows from the{" "}
          <Link
            href="/events"
            className="text-text-primary underline underline-offset-2"
          >
            calendar
          </Link>{" "}
          and we&apos;ll find more like them.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-bg-surface p-8 text-center">
      <h2 className="text-lg font-semibold text-text-primary">
        No matches yet
      </h2>
      <p className="mt-2 text-sm text-text-secondary">
        We couldn&apos;t find upcoming DMV shows that match your taste right
        now. The next nightly scrape may surface new fits — until then, browse
        the{" "}
        <Link
          href="/events"
          className="text-text-primary underline underline-offset-2"
        >
          full calendar
        </Link>
        .
      </p>
    </div>
  );
}
