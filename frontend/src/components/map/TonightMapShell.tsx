/**
 * Tonight map shell — glues the FilterBar and TonightMap together.
 *
 * Owns the active-bucket state and re-fetches ``/maps/tonight`` with
 * the bucket's genre list whenever the user toggles a pill. Initial
 * (unfiltered) pins come from the server component in ``page.tsx`` so
 * there's no spinner on first paint.
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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

  useEffect(() => {
    let cancelled = false;
    const genres = genresForBucket(activeBucket);
    if (!genres) {
      setEvents(initialEvents);
      return;
    }
    setIsFetching(true);
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
        if (!cancelled) setIsFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeBucket, initialEvents]);

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
      <div className={isFetching ? "opacity-80 transition" : "transition"}>
        <TonightMap
          events={events}
          recommendations={recommendations}
          activeBucket={activeBucket}
        />
      </div>
    </div>
  );
}
