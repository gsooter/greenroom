/**
 * /saved — the authenticated user's saved shows.
 *
 * CSR only — the list is private, and revalidation happens whenever
 * the user saves or unsaves an event elsewhere in the app. Server
 * rendering would leak a shared cache across accounts.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import { listSavedEvents, unsaveEvent } from "@/lib/api/saved-events";
import { useRequireAuth } from "@/lib/auth";
import type { Paginated, SavedEvent } from "@/types";

export default function SavedPage(): JSX.Element {
  const { token, isAuthenticated, isLoading } = useRequireAuth();
  const [saved, setSaved] = useState<Paginated<SavedEvent> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!token) return;
    try {
      const res = await listSavedEvents(token);
      setSaved(res);
      setError(null);
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : "Could not load saved shows.";
      setError(message);
    }
  }, [token]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void load();
  }, [isAuthenticated, load]);

  const handleUnsave = useCallback(
    async (eventId: string): Promise<void> => {
      if (!token) return;
      setPendingId(eventId);
      try {
        await unsaveEvent(token, eventId);
        await load();
      } catch (err) {
        const message =
          err instanceof ApiRequestError
            ? err.message
            : "Could not unsave that show.";
        setError(message);
      } finally {
        setPendingId(null);
      }
    },
    [token, load],
  );

  if (isLoading || !isAuthenticated) {
    return <PageShell>Loading your saved shows…</PageShell>;
  }

  if (error) {
    return <PageShell>Something went wrong: {error}</PageShell>;
  }

  if (!saved) {
    return <PageShell>Loading your saved shows…</PageShell>;
  }

  if (saved.data.length === 0) {
    return (
      <PageShell>
        <h1 className="text-2xl font-semibold text-text-primary">
          No saved shows yet
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          Browse the{" "}
          <Link href="/events" className="underline underline-offset-2">
            DMV calendar
          </Link>{" "}
          and tap the save icon on any show to pin it here.
        </p>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <h1 className="text-2xl font-semibold text-text-primary">Saved shows</h1>
      <ul className="mt-6 space-y-3">
        {saved.data.map((entry) => (
          <li
            key={entry.event.id}
            className="flex items-center justify-between gap-4 rounded-xl border border-border bg-bg-surface px-4 py-3"
          >
            <Link
              href={`/events/${entry.event.id}`}
              className="flex-1 text-sm font-medium text-text-primary"
            >
              <span className="block truncate">{entry.event.title}</span>
              <span className="mt-1 block text-xs text-text-secondary">
                {entry.event.venue?.name ?? "TBA"}
                {entry.event.starts_at
                  ? ` · ${new Date(entry.event.starts_at).toLocaleDateString()}`
                  : ""}
              </span>
            </Link>
            <button
              type="button"
              onClick={() => void handleUnsave(entry.event.id)}
              disabled={pendingId === entry.event.id}
              className="rounded-md border border-border px-3 py-1 text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"
            >
              {pendingId === entry.event.id ? "Removing…" : "Remove"}
            </button>
          </li>
        ))}
      </ul>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12">{children}</main>
  );
}
