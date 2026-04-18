/**
 * /saved — the authenticated user's saved shows.
 *
 * CSR only — the list is private, and revalidation happens via the
 * shared `SavedEventsContext`, which drives the heart state on every
 * EventCard too. That lets an unsave here flip the card on the browse
 * view without a reload.
 */

"use client";

import Link from "next/link";

import EventCard from "@/components/events/EventCard";
import { useRequireAuth } from "@/lib/auth";
import { useSavedEvents } from "@/lib/saved-events-context";

export default function SavedPage(): JSX.Element {
  const { isAuthenticated, isLoading } = useRequireAuth();
  const { savedEvents, isLoading: isSavedLoading, isReady } = useSavedEvents();

  if (isLoading || !isAuthenticated) {
    return <PageShell>Loading your saved shows…</PageShell>;
  }

  if (!isReady && isSavedLoading) {
    return <PageShell>Loading your saved shows…</PageShell>;
  }

  if (savedEvents.length === 0) {
    return (
      <PageShell>
        <header className="mb-6">
          <h1 className="text-3xl font-semibold text-text-primary">
            Saved shows
          </h1>
        </header>
        <div className="rounded-xl border border-border bg-bg-surface p-8 text-center">
          <h2 className="text-lg font-semibold text-text-primary">
            No saved shows yet
          </h2>
          <p className="mt-2 text-sm text-text-secondary">
            Browse the{" "}
            <Link
              href="/events"
              className="text-text-primary underline underline-offset-2"
            >
              DMV calendar
            </Link>{" "}
            and tap the save icon on any show to pin it here.
          </p>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <header className="mb-8">
        <h1 className="text-3xl font-semibold text-text-primary">
          Saved shows
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {savedEvents.length} show{savedEvents.length === 1 ? "" : "s"} pinned
          to your list.
        </p>
      </header>

      <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {savedEvents.map((entry) => (
          <li key={entry.event.id}>
            <EventCard event={entry.event} />
          </li>
        ))}
      </ul>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }): JSX.Element {
  return <main className="mx-auto max-w-6xl px-6 py-12">{children}</main>;
}
