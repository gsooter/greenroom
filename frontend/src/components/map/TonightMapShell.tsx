/**
 * Tonight map shell — glues the FilterBar and TonightMap together.
 *
 * Owns the active-bucket state and re-fetches ``/maps/tonight`` with
 * the bucket's genre list whenever the user toggles a pill. Initial
 * (unfiltered) pins come from the server component in ``page.tsx`` so
 * there's no spinner on first paint.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import FilterBar, {
  TONIGHT_GENRE_BUCKETS,
  genresForBucket,
} from "@/components/map/FilterBar";
import TonightMap from "@/components/map/TonightMap";
import { getTonightMap } from "@/lib/api/maps";
import {
  pinColorForGenres,
  type MapPinColor,
} from "@/lib/genre-colors";
import type { MapRecommendation, TonightMapEvent } from "@/types";

// How long to keep the "Updating…" indicator visible after the fetch
// resolves. MapKit JS annotation reconciliation runs after React commits
// the new events, and in Chrome it can lag several hundred milliseconds
// behind Safari. Holding the indicator briefly covers that gap so the
// user sees a consistent "thinking" signal until the pins actually move.
const SETTLE_MS = 400;

interface TonightMapShellProps {
  initialEvents: TonightMapEvent[];
  recommendations: MapRecommendation[];
}

/**
 * Client wrapper around {@link TonightMap}.
 *
 * @param initialEvents - Server-fetched, unfiltered tonight's events.
 * @param recommendations - Community recommendation overlay rows.
 */
export default function TonightMapShell({
  initialEvents,
  recommendations,
}: TonightMapShellProps): JSX.Element {
  const [activeBucket, setActiveBucket] = useState<MapPinColor | null>(null);
  const [events, setEvents] = useState<TonightMapEvent[]>(initialEvents);
  const [isFetching, setIsFetching] = useState<boolean>(false);
  const [isSettling, setIsSettling] = useState<boolean>(false);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (settleTimer.current) clearTimeout(settleTimer.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const genres = genresForBucket(activeBucket);
    if (!genres) {
      setEvents(initialEvents);
      return;
    }
    setIsFetching(true);
    if (settleTimer.current) {
      clearTimeout(settleTimer.current);
      settleTimer.current = null;
    }
    getTonightMap({ genres: [...genres] })
      .then((envelope) => {
        if (cancelled) return;
        setEvents(envelope.data);
      })
      .catch(() => {
        if (cancelled) return;
        setEvents([]);
      })
      .finally(() => {
        if (cancelled) return;
        setIsFetching(false);
        setIsSettling(true);
        settleTimer.current = setTimeout(() => {
          setIsSettling(false);
          settleTimer.current = null;
        }, SETTLE_MS);
      });
    return () => {
      cancelled = true;
    };
  }, [activeBucket, initialEvents]);

  const isUpdating = isFetching || isSettling;

  const counts = useMemo(() => {
    const byBucket: Partial<Record<MapPinColor, number>> = {};
    for (const bucket of TONIGHT_GENRE_BUCKETS) {
      byBucket[bucket.key] = 0;
    }
    for (const event of initialEvents) {
      const color = pinColorForGenres(event.genres);
      byBucket[color] = (byBucket[color] ?? 0) + 1;
    }
    return { ...byBucket, total: initialEvents.length };
  }, [initialEvents]);

  const onChange = useCallback((bucket: MapPinColor | null) => {
    setActiveBucket(bucket);
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <FilterBar
        activeBucket={activeBucket}
        onChange={onChange}
        counts={counts}
      />
      <div className="relative">
        <div
          className={
            "transition-opacity duration-200 " +
            (isUpdating ? "opacity-70" : "opacity-100")
          }
        >
          <TonightMap
            events={events}
            recommendations={recommendations}
            activeBucket={activeBucket}
          />
        </div>
        {isUpdating ? (
          <div
            role="status"
            aria-live="polite"
            className="pointer-events-none absolute right-3 top-3 z-10 flex items-center gap-2 rounded-full border border-border bg-bg-white/95 px-3 py-1.5 text-xs font-medium text-text-primary shadow-sm backdrop-blur"
          >
            <span
              aria-hidden
              className="h-3 w-3 animate-spin rounded-full border-2 border-border border-t-green-primary"
            />
            Updating map…
          </div>
        ) : null}
      </div>
    </div>
  );
}
