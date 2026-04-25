/**
 * /for-you — personalized recommendations.
 *
 * CSR-only: the list is per-user, keyed off the session JWT in
 * localStorage, so there is nothing to SSR. The recommendation engine
 * lazy-generates on the server the first time this page's GET fires
 * after login; subsequent visits hit the already-persisted rows.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import ForYouEmptyState, {
  type ForYouEmptyVariant,
} from "@/components/recommendations/ForYouEmptyState";
import RecommendationCard from "@/components/recommendations/RecommendationCard";
import RecommendationGridSkeleton from "@/components/recommendations/RecommendationGridSkeleton";
import { getMe, getMyMusicConnections } from "@/lib/api/me";
import {
  listRecommendations,
  refreshRecommendations,
} from "@/lib/api/recommendations";
import { useRequireAuth } from "@/lib/auth";
import { bucketizeRecommendations } from "@/lib/recommendations/bucket";
import type { Recommendation } from "@/types";

const PER_PAGE = 24;

type Status = "idle" | "loading" | "ready" | "empty" | "error";

export default function ForYouPage(): JSX.Element {
  const { isAuthenticated, isLoading, token } = useRequireAuth();

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [emptyVariant, setEmptyVariant] =
    useState<ForYouEmptyVariant>("no_matches");
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async (activeToken: string): Promise<void> => {
    setStatus("loading");
    setErrorMessage(null);
    try {
      const page = await listRecommendations(activeToken, {
        perPage: PER_PAGE,
      });
      const unique = dedupeRecommendations(page.data);
      setRecs(unique);
      if (unique.length > 0) {
        setStatus("ready");
        return;
      }
      const variant = await resolveEmptyVariant(activeToken);
      setEmptyVariant(variant);
      setStatus("empty");
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

  const buckets = status === "ready" ? bucketizeRecommendations(recs) : [];

  return (
    <div className="py-6">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-text-primary">For you</h1>
          <p className="mt-1 text-sm text-text-secondary">
            DMV shows picked from the artists in your rotation and the venues
            you&apos;ve saved.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={isRefreshing || status === "loading"}
          className="rounded-md border border-border bg-bg-surface px-3 py-1.5 text-sm font-medium text-text-primary transition hover:border-green-primary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isRefreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {status === "loading" ? <RecommendationGridSkeleton /> : null}

      {status === "error" ? (
        <div className="rounded-lg border border-blush-accent/40 bg-blush-soft/50 p-4 text-sm text-blush-accent">
          {errorMessage ?? "Something went wrong."}
        </div>
      ) : null}

      {status === "empty" ? <ForYouEmptyState variant={emptyVariant} /> : null}

      {status === "ready" && buckets.length > 0 ? (
        <div className="space-y-10">
          {buckets.map((bucket) => (
            <section key={bucket.key}>
              <h2 className="mb-4 flex items-baseline gap-2 text-lg font-semibold text-text-primary">
                {bucket.label}
                <span className="text-sm font-normal text-text-secondary">
                  {bucket.recommendations.length}
                </span>
              </h2>
              <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {bucket.recommendations.map((rec) => (
                  <li key={rec.id}>
                    <RecommendationCard recommendation={rec} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Decides whether an empty For-You list is "you have no signal we can
 * score on" or "you have signal but nothing matched this week". Falls
 * back to ``no_matches`` if the supporting calls error so we never
 * accidentally tell a connected user to connect a service.
 */
async function resolveEmptyVariant(
  token: string,
): Promise<ForYouEmptyVariant> {
  try {
    const [user, connections] = await Promise.all([
      getMe(token),
      getMyMusicConnections(token),
    ]);
    const hasConnectedService = connections.connections.some(
      (c) => c.connected && c.artist_count > 0,
    );
    const hasGenrePicks = user.genre_preferences.length > 0;
    if (hasConnectedService || hasGenrePicks) return "no_matches";
    return "no_signal";
  } catch {
    return "no_matches";
  }
}

/**
 * Drops cards that point at the same real-world show. Mirrors the
 * backend dedupe in :func:`backend.recommendations.engine._dedupe_by_show`
 * so a presale + general-sale row never both render on this page even
 * if a stale generation slipped both through.
 */
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
