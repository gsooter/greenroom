/**
 * Tonight map — MapKit JS client component.
 *
 * Receives a pre-fetched list of tonight's DMV events (from the
 * ``/map`` page's server component) plus an optional recommendations
 * overlay and renders a full-bleed MapKit JS map with one pin per
 * event. Pins are colored by genre using the bucket table in
 * ``@/lib/genre-colors`` so the color of a pin mirrors the color of
 * the active filter pill above the map.
 *
 * The MapKit developer token is loaded lazily on mount via
 * ``/api/v1/maps/token``. If the backend reports
 * ``APPLE_MAPS_UNAVAILABLE`` — the environment has no signing key —
 * the component renders a fallback list of tonight's pins instead,
 * so the page remains usable.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getMapKitToken } from "@/lib/api/maps";
import {
  pinColorForGenres,
  pinColorVariable,
  type MapPinColor,
} from "@/lib/genre-colors";
import { initMapKit, type MapKitMap, type MapKitStatic } from "@/lib/mapkit";
import type { MapRecommendation, TonightMapEvent } from "@/types";

const DC_CENTER = { latitude: 38.9072, longitude: -77.0369 };
const DEFAULT_SPAN = { latitudeDelta: 0.28, longitudeDelta: 0.4 };

type LoadState = "idle" | "loading" | "ready" | "unavailable";

interface TonightMapProps {
  events: TonightMapEvent[];
  recommendations?: MapRecommendation[];
  activeBucket?: MapPinColor | null;
}

/**
 * Render the MapKit JS map and its pins.
 *
 * @param events - Tonight's DMV events (pre-filtered to ones with coords).
 * @param recommendations - Optional community recommendation overlay.
 * @param activeBucket - Currently-filtered genre bucket. Only used for the
 *   empty-state copy — the backend filter is applied upstream.
 */
export default function TonightMap({
  events,
  recommendations = [],
  activeBucket = null,
}: TonightMapProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapKitMap | null>(null);
  const mapkitRef = useRef<MapKitStatic | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [selected, setSelected] = useState<TonightMapEvent | null>(null);

  const fetchToken = useCallback(async (): Promise<string> => {
    const origin =
      typeof window === "undefined" ? undefined : window.location.origin;
    const { token } = await getMapKitToken({ origin, revalidateSeconds: 300 });
    return token;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    initMapKit(fetchToken)
      .then((mk) => {
        if (cancelled || !containerRef.current) return;
        mapkitRef.current = mk;
        const map = new mk.Map(containerRef.current, {
          showsCompass: mk.FeatureVisibility.Hidden,
          showsZoomControl: true,
          showsMapTypeControl: false,
          showsUserLocationControl: false,
          colorScheme: mk.Map.ColorSchemes.Light,
        });
        map.region = new mk.CoordinateRegion(
          new mk.Coordinate(DC_CENTER.latitude, DC_CENTER.longitude),
          new mk.CoordinateSpan(
            DEFAULT_SPAN.latitudeDelta,
            DEFAULT_SPAN.longitudeDelta,
          ),
        );
        mapRef.current = map;
        setLoadState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.error("[TonightMap] MapKit init or construction failed:", err);
        setLoadState("unavailable");
      });

    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.destroy();
        mapRef.current = null;
      }
    };
  }, [fetchToken]);

  useEffect(() => {
    const map = mapRef.current;
    const mk = mapkitRef.current;
    if (!map || !mk || loadState !== "ready") return;

    const annotations = events.map((event) => {
      const color = pinColorForGenres(event.genres);
      const coord = new mk.Coordinate(event.venue.latitude, event.venue.longitude);
      const annotation = new mk.Annotation(
        coord,
        () => renderPinElement(color),
        {
          title: event.title,
          subtitle: event.venue.name,
          data: { eventId: event.id },
        },
      );
      annotation.addEventListener("select", () => setSelected(event));
      annotation.addEventListener("deselect", () => setSelected(null));
      return annotation;
    });

    const recAnnotations = recommendations.map((rec) => {
      const coord = new mk.Coordinate(rec.latitude, rec.longitude);
      return new mk.Annotation(
        coord,
        () => renderRecommendationElement(),
        {
          title: rec.place_name,
          subtitle: rec.category,
          data: { recommendationId: rec.id },
        },
      );
    });

    const all = [...annotations, ...recAnnotations];
    if (all.length > 0) map.addAnnotations(all);

    return () => {
      // The init effect's cleanup can destroy the map before this cleanup
      // runs (unmount, token refresh, strict-mode remount). Calling
      // removeAnnotations on a destroyed map throws inside MapKit — skip
      // if the live ref no longer points to the map we captured.
      if (all.length === 0 || mapRef.current !== map) return;
      try {
        map.removeAnnotations(all);
      } catch {
        /* map was torn down between renders — nothing to clean up */
      }
    };
  }, [events, recommendations, loadState]);

  const hasEvents = events.length > 0;
  const emptyMessage = useMemo(() => {
    if (hasEvents) return null;
    if (activeBucket) {
      return "No pins match that genre tonight. Try another bucket or clear the filter.";
    }
    return "No mappable shows tonight. Check back tomorrow — the crawl runs nightly.";
  }, [hasEvents, activeBucket]);

  if (loadState === "unavailable") {
    return <MapFallbackList events={events} />;
  }

  return (
    <div className="relative h-[70vh] min-h-[420px] w-full overflow-hidden rounded-lg border border-border bg-bg-surface">
      <div ref={containerRef} className="absolute inset-0" aria-hidden />
      {loadState !== "ready" ? (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-text-secondary">
          Loading map…
        </div>
      ) : null}
      {emptyMessage ? (
        <div className="pointer-events-none absolute inset-x-0 top-3 mx-auto w-fit rounded-full border border-border bg-bg-white/95 px-4 py-1.5 text-xs font-medium text-text-secondary shadow-sm">
          {emptyMessage}
        </div>
      ) : null}
      {selected ? (
        <SelectedEventCard event={selected} onDismiss={() => setSelected(null)} />
      ) : null}
    </div>
  );
}

function renderPinElement(color: MapPinColor): HTMLElement {
  const wrap = document.createElement("div");
  wrap.setAttribute(
    "style",
    [
      "position:relative",
      "width:22px",
      "height:22px",
      "transform:translate(-50%, -100%)",
    ].join(";"),
  );
  const pulse = document.createElement("span");
  pulse.setAttribute(
    "style",
    [
      "position:absolute",
      "inset:-6px",
      "border-radius:999px",
      `background:${pinColorVariable(color)}`,
      "opacity:0.3",
      "animation:greenroom-pin-pulse 1.6s ease-out infinite",
    ].join(";"),
  );
  const dot = document.createElement("span");
  dot.setAttribute(
    "style",
    [
      "position:absolute",
      "inset:0",
      "border-radius:999px",
      `background:${pinColorVariable(color)}`,
      "border:2px solid var(--color-bg-white)",
      "box-shadow:0 2px 6px rgba(0,0,0,0.25)",
    ].join(";"),
  );
  wrap.append(pulse, dot);
  return wrap;
}

function renderRecommendationElement(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.setAttribute(
    "style",
    [
      "width:14px",
      "height:14px",
      "border-radius:999px",
      "background:var(--color-blush-accent)",
      "border:2px solid var(--color-bg-white)",
      "transform:translate(-50%, -50%)",
      "box-shadow:0 1px 3px rgba(0,0,0,0.2)",
    ].join(";"),
  );
  return wrap;
}

interface SelectedEventCardProps {
  event: TonightMapEvent;
  onDismiss: () => void;
}

function SelectedEventCard({
  event,
  onDismiss,
}: SelectedEventCardProps): JSX.Element {
  return (
    <div className="absolute inset-x-3 bottom-3 flex items-start justify-between gap-3 rounded-lg border border-border bg-bg-white/95 p-3 shadow-lg backdrop-blur">
      <div className="flex min-w-0 flex-col">
        <a
          href={`/events/${event.slug}`}
          className="truncate text-sm font-semibold text-text-primary hover:text-accent"
        >
          {event.title}
        </a>
        <span className="truncate text-xs text-text-secondary">
          {event.venue.name}
          {event.artists.length > 0 ? ` · ${event.artists.slice(0, 2).join(", ")}` : ""}
        </span>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Close event preview"
        className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-medium text-text-secondary hover:border-green-primary hover:text-accent"
      >
        Close
      </button>
    </div>
  );
}

/**
 * Textual fallback rendered when MapKit JS can't load (usually because
 * the environment has no Apple Maps credentials). Shows the same pins
 * as a plain list so the page still carries real content.
 */
function MapFallbackList({ events }: { events: TonightMapEvent[] }): JSX.Element {
  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-surface p-6 text-center text-sm text-text-secondary">
        No mappable shows tonight.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
        Map unavailable — showing tonight&apos;s pins as a list
      </p>
      <ul className="flex flex-col gap-2">
        {events.map((event) => (
          <li
            key={event.id}
            className="flex items-start justify-between gap-3 rounded-md bg-bg-white px-3 py-2"
          >
            <div className="flex min-w-0 flex-col">
              <a
                href={`/events/${event.slug}`}
                className="truncate text-sm font-semibold text-text-primary hover:text-accent"
              >
                {event.title}
              </a>
              <span className="truncate text-xs text-text-secondary">
                {event.venue.name}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
