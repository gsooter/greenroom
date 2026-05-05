"use client";

/**
 * Route-segment error boundary.
 *
 * Catches errors thrown during rendering of any route under the root
 * layout. Reports them to Sentry (no-op when DSN unset) and gives the
 * user a way to retry without a full reload. The root layout — and
 * therefore the AppShell, nav, and providers — stays mounted.
 */

import { useEffect } from "react";

interface ErrorBoundaryProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorBoundary({ error, reset }: ErrorBoundaryProps) {
  useEffect(() => {
    // Sentry is loaded lazily so the bundler doesn't need to resolve
    // `@sentry/nextjs` when no DSN is configured (local dev). The
    // DefinePlugin replaces the env check with the literal at build
    // time, so the entire branch is dead-code-eliminated when DSN is
    // empty and the dynamic import is dropped from the bundle.
    if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
      void import("@sentry/nextjs").then((Sentry) => {
        Sentry.captureException(error);
      });
    }
  }, [error]);

  return (
    <main className="mx-auto flex max-w-xl flex-col items-center gap-4 px-6 py-16 text-center">
      <h1 className="text-2xl font-semibold text-foreground">
        Something went wrong
      </h1>
      <p className="text-sm text-muted">
        We hit an unexpected error loading this page. The team has been
        notified — try again, or head back to the calendar.
      </p>
      {error.digest ? (
        <p className="text-xs text-muted">Reference: {error.digest}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
        <button
          type="button"
          onClick={reset}
          className="rounded-full bg-green-primary px-5 py-2 text-sm font-medium text-text-inverse transition hover:bg-green-dark"
        >
          Try again
        </button>
        <a
          href="/events"
          className="rounded-full border border-border px-5 py-2 text-sm font-medium text-foreground transition hover:bg-bg-surface"
        >
          Back to events
        </a>
      </div>
    </main>
  );
}
