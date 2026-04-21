/**
 * /for-you — personalized recommendations.
 *
 * CSR-only: the list is per-user, keyed off the session JWT in
 * localStorage, so there is nothing to SSR. The recommendation engine
 * lazy-generates on the server the first time this page's GET fires
 * after login; subsequent visits hit the already-persisted rows.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import EventCard from "@/components/events/EventCard";
import { useRequireAuth } from "@/lib/auth";
import {
  listRecommendations,
  refreshRecommendations,
} from "@/lib/api/recommendations";
import type { Recommendation } from "@/types";

const PER_PAGE = 24;

export default function ForYouPage(): JSX.Element {
  const { isAuthenticated, isLoading, token } = useRequireAuth();

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async (activeToken: string): Promise<void> => {
    setStatus("loading");
    setErrorMessage(null);
    try {
      const page = await listRecommendations(activeToken, {
        perPage: PER_PAGE,
      });
      setRecs(dedupeRecommendations(page.data));
      setStatus("ready");
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "We couldn't load your recommendations.",
      );
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    void load(token);
  }, [token, load]);

  const handleRefresh = useCallback(async (): Promise<void> => {
    if (!token) return;
    setIsRefreshing(true);
    setErrorMessage(null);
    try {
      await refreshRecommendations(token);
      await load(token);
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "Refresh failed — try again in a moment.",
      );
    } finally {
      setIsRefreshing(false);
    }
  }, [token, load]);

  if (isLoading || !isAuthenticated) {
    return (
      <p className="py-12 text-sm text-text-secondary">
        Loading your recommendations…
      </p>
    );
  }

  const showEmpty = status === "ready" && recs.length === 0;
  const showList = status === "ready" && recs.length > 0;

  return (
    <div className="py-6">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-text-primary">For you</h1>
          <p className="mt-1 text-sm text-text-secondary">
            DMV shows picked from the artists in your Spotify rotation.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={isRefreshing}
          className="rounded-md border border-border bg-bg-surface px-3 py-1.5 text-sm font-medium text-text-primary transition hover:border-green-primary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isRefreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {status === "loading" ? (
        <p className="text-sm text-text-secondary">
          Scoring the DMV calendar for you…
        </p>
      ) : null}

      {status === "error" ? (
        <div className="rounded-lg border border-blush-accent/40 bg-blush-soft/50 p-4 text-sm text-blush-accent">
          {errorMessage ?? "Something went wrong."}
        </div>
      ) : null}

      {showEmpty ? <EmptyState /> : null}

      {showList ? (
        <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {recs.map((rec) => (
            <li key={rec.id} className="flex flex-col gap-2">
              <EventCard event={rec.event} />
              <ReasonChips reasons={rec.match_reasons} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function EmptyState(): JSX.Element {
  return (
    <div className="rounded-xl border border-border bg-bg-surface p-8 text-center">
      <h2 className="text-lg font-semibold text-text-primary">
        No matches yet
      </h2>
      <p className="mt-2 text-sm text-text-secondary">
        We couldn&apos;t find upcoming DMV shows that match your current top
        artists. Once our next nightly scrape lands, check back — or browse the{" "}
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

function ReasonChips({
  reasons,
}: {
  reasons: Recommendation["match_reasons"];
}): JSX.Element | null {
  if (!reasons || reasons.length === 0) return null;
  const unique: Recommendation["match_reasons"] = [];
  const seen = new Set<string>();
  for (const reason of reasons) {
    const key = (
      reason.artist_name ??
      reason.genre_slug ??
      reason.genre ??
      reason.label
    ).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(reason);
    if (unique.length >= 3) break;
  }

  return (
    <ul className="flex flex-wrap gap-2">
      {unique.map((reason) => (
        <li
          key={`${reason.scorer}:${reason.kind}:${reason.label}`}
          className="rounded-full bg-blush-soft px-3 py-1 text-xs font-medium text-blush-accent"
        >
          {reason.label}
        </li>
      ))}
    </ul>
  );
}

function dedupeRecommendations(recs: Recommendation[]): Recommendation[] {
  const seen = new Set<string>();
  const unique: Recommendation[] = [];
  for (const rec of recs) {
    const event = rec.event;
    const venueId = event.venue?.id ?? "";
    const key = `${venueId}|${event.title.trim().toLowerCase()}|${event.starts_at ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(rec);
  }
  return unique;
}
