/**
 * Expanded map modal for a venue's surroundings.
 *
 * Opens from the {@link VenueSurroundings} card and shows an interactive
 * Apple MapKit JS map layered with three kinds of pins:
 *
 * * The venue itself, rendered as a pulsing green dot.
 * * Community food/drink tips (blush).
 * * Apple-fetched nearby POIs (navy).
 *
 * A side/bottom list panel mirrors the pins and is synced with
 * selection — clicking a pin highlights the list row, and clicking a
 * list row selects the pin and centers the map on it.
 *
 * Fails soft: if MapKit JS can't load (no credentials, offline, etc.)
 * the modal renders only the list panel so the user still gets the
 * content.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getMapKitToken } from "@/lib/api/maps";
import type { NearbyPoi } from "@/lib/api/venues";
import {
  initMapKit,
  type MapKitAnnotation,
  type MapKitMap,
  type MapKitStatic,
} from "@/lib/mapkit";
import type { MapRecommendation } from "@/types";

type MapLoadState = "idle" | "loading" | "ready" | "unavailable";

type ItemKind = "venue" | "tip" | "poi";

interface MapListItem {
  id: string;
  kind: ItemKind;
  title: string;
  subtitle: string | null;
  latitude: number;
  longitude: number;
  distanceM: number | null;
}

interface VenueSurroundingsModalProps {
  venueName: string;
  venueLatitude: number;
  venueLongitude: number;
  venueAddress: string | null;
  tips: MapRecommendation[];
  nearbyPois: NearbyPoi[];
  onClose: () => void;
}

/**
 * Render the expanded surroundings modal.
 *
 * @param venueName - Display name used in the header and venue pin label.
 * @param venueLatitude - Venue latitude (also the initial map center).
 * @param venueLongitude - Venue longitude (also the initial map center).
 * @param venueAddress - Street address, used in the list panel row.
 * @param tips - Community tips already loaded by the parent card.
 * @param nearbyPois - Apple-fetched POIs already loaded by the parent card.
 * @param onClose - Called when the user dismisses via backdrop, close
 *     button, or the ``Escape`` key.
 */
export default function VenueSurroundingsModal({
  venueName,
  venueLatitude,
  venueLongitude,
  venueAddress,
  tips,
  nearbyPois,
  onClose,
}: VenueSurroundingsModalProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapKitMap | null>(null);
  const mapkitRef = useRef<MapKitStatic | null>(null);
  const annotationRefs = useRef<Map<string, MapKitAnnotation>>(new Map());
  const [loadState, setLoadState] = useState<MapLoadState>("idle");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const listItemRefs = useRef<Map<string, HTMLLIElement | null>>(new Map());

  const items = useMemo<MapListItem[]>(() => {
    const venueItem: MapListItem = {
      id: "venue",
      kind: "venue",
      title: venueName,
      subtitle: venueAddress,
      latitude: venueLatitude,
      longitude: venueLongitude,
      distanceM: null,
    };
    const tipItems: MapListItem[] = tips.map((tip) => ({
      id: `tip-${tip.id}`,
      kind: "tip",
      title: tip.place_name,
      subtitle: tip.body,
      latitude: tip.latitude,
      longitude: tip.longitude,
      distanceM: tip.distance_from_venue_m ?? null,
    }));
    const poiItems: MapListItem[] = nearbyPois.map((poi) => ({
      id: `poi-${poi.latitude.toFixed(5)}-${poi.longitude.toFixed(5)}`,
      kind: "poi",
      title: poi.name,
      subtitle: poi.category + (poi.address ? ` · ${poi.address}` : ""),
      latitude: poi.latitude,
      longitude: poi.longitude,
      distanceM: poi.distance_m,
    }));
    return [venueItem, ...tipItems, ...poiItems];
  }, [
    venueName,
    venueAddress,
    venueLatitude,
    venueLongitude,
    tips,
    nearbyPois,
  ]);

  const fetchToken = useCallback(async (): Promise<string> => {
    const origin =
      typeof window === "undefined" ? undefined : window.location.origin;
    const { token } = await getMapKitToken({ origin, revalidateSeconds: 300 });
    return token;
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

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
          new mk.Coordinate(venueLatitude, venueLongitude),
          new mk.CoordinateSpan(0.012, 0.018),
        );
        mapRef.current = map;
        setLoadState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.error("[VenueSurroundingsModal] MapKit init failed:", err);
        setLoadState("unavailable");
      });

    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.destroy();
        mapRef.current = null;
      }
    };
  }, [fetchToken, venueLatitude, venueLongitude]);

  useEffect(() => {
    const map = mapRef.current;
    const mk = mapkitRef.current;
    if (!map || !mk || loadState !== "ready") return;

    const created: MapKitAnnotation[] = [];
    annotationRefs.current.clear();

    for (const item of items) {
      const coord = new mk.Coordinate(item.latitude, item.longitude);
      const annotation = new mk.Annotation(
        coord,
        () => renderMarkerElement(item.kind),
        {
          title: item.title,
          subtitle: item.subtitle ?? "",
          data: { itemId: item.id },
        },
      );
      annotation.addEventListener("select", () => setSelectedId(item.id));
      annotation.addEventListener("deselect", () =>
        setSelectedId((current) => (current === item.id ? null : current)),
      );
      created.push(annotation);
      annotationRefs.current.set(item.id, annotation);
    }

    if (created.length > 0) map.addAnnotations(created);

    return () => {
      if (created.length === 0 || mapRef.current !== map) return;
      try {
        map.removeAnnotations(created);
      } catch {
        /* map torn down between renders */
      }
    };
  }, [items, loadState]);

  useEffect(() => {
    if (!selectedId) return;
    const row = listItemRefs.current.get(selectedId);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  const handleRowClick = useCallback(
    (item: MapListItem): void => {
      setSelectedId(item.id);
      const map = mapRef.current;
      const mk = mapkitRef.current;
      if (!map || !mk) return;
      map.region = new mk.CoordinateRegion(
        new mk.Coordinate(item.latitude, item.longitude),
        new mk.CoordinateSpan(0.008, 0.012),
      );
      const annotation = annotationRefs.current.get(item.id);
      if (annotation) annotation.selected = true;
    },
    [],
  );

  const registerRow = useCallback(
    (id: string) =>
      (el: HTMLLIElement | null): void => {
        if (el) listItemRefs.current.set(id, el);
        else listItemRefs.current.delete(id);
      },
    [],
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Map around ${venueName}`}
      className="fixed inset-0 z-50 flex flex-col bg-bg-base/95 backdrop-blur-sm sm:p-6"
    >
      <button
        type="button"
        aria-label="Close map"
        onClick={onClose}
        className="absolute inset-0 -z-10 cursor-default"
        tabIndex={-1}
      />
      <div className="relative flex flex-1 flex-col overflow-hidden rounded-none border-0 bg-bg-white shadow-xl sm:rounded-xl sm:border sm:border-border">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex min-w-0 flex-col">
            <h2 className="truncate text-base font-semibold text-text-primary">
              Around {venueName}
            </h2>
            <p className="truncate text-xs text-text-secondary">
              {tips.length} {tips.length === 1 ? "tip" : "tips"} ·{" "}
              {nearbyPois.length}{" "}
              {nearbyPois.length === 1 ? "spot" : "spots"} nearby
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-primary transition hover:border-green-primary hover:text-green-primary"
          >
            Close
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div className="relative h-[55%] min-h-[280px] w-full bg-bg-surface lg:h-auto lg:flex-1">
            <div ref={containerRef} className="absolute inset-0" aria-hidden />
            {loadState !== "ready" && loadState !== "unavailable" ? (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-text-secondary">
                Loading map…
              </div>
            ) : null}
            {loadState === "unavailable" ? (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-text-secondary">
                Map unavailable — use the list to explore the area.
              </div>
            ) : null}
          </div>
          <aside className="flex max-h-[45%] min-h-0 flex-col border-t border-border bg-bg-white lg:max-h-none lg:w-[360px] lg:border-l lg:border-t-0">
            <div className="flex items-center gap-3 px-4 py-2 text-xs text-text-secondary">
              <LegendDot kind="venue" />
              <span>Venue</span>
              <LegendDot kind="tip" />
              <span>Tips</span>
              <LegendDot kind="poi" />
              <span>Nearby</span>
            </div>
            <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
              {items.map((item) => (
                <li
                  key={item.id}
                  ref={registerRow(item.id)}
                  className={
                    "flex cursor-pointer flex-col gap-1 px-4 py-3 transition " +
                    (selectedId === item.id
                      ? "bg-bg-surface"
                      : "hover:bg-bg-surface/60")
                  }
                  onClick={() => handleRowClick(item)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <LegendDot kind={item.kind} />
                      <span className="truncate text-sm font-semibold text-text-primary">
                        {item.title}
                      </span>
                    </div>
                    {item.distanceM !== null ? (
                      <span className="shrink-0 text-xs text-text-secondary">
                        {formatDistance(item.distanceM)}
                      </span>
                    ) : null}
                  </div>
                  {item.subtitle ? (
                    <p className="line-clamp-2 text-xs text-text-secondary">
                      {item.subtitle}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </aside>
        </div>
      </div>
    </div>
  );
}

/**
 * Build the pin HTML element for the given item kind.
 *
 * @param kind - Which kind of pin to draw.
 * @returns A detached ``HTMLElement`` suitable for MapKit's annotation
 *     factory.
 */
function renderMarkerElement(kind: ItemKind): HTMLElement {
  const wrap = document.createElement("div");
  const colorVar =
    kind === "venue"
      ? "var(--color-green-primary)"
      : kind === "tip"
        ? "var(--color-blush-accent)"
        : "var(--color-navy-dark)";
  const size = kind === "venue" ? 22 : 14;
  wrap.setAttribute(
    "style",
    [
      "position:relative",
      `width:${size}px`,
      `height:${size}px`,
      "transform:translate(-50%, -50%)",
    ].join(";"),
  );
  if (kind === "venue") {
    const pulse = document.createElement("span");
    pulse.setAttribute(
      "style",
      [
        "position:absolute",
        "inset:-6px",
        "border-radius:999px",
        `background:${colorVar}`,
        "opacity:0.3",
        "animation:greenroom-pin-pulse 1.6s ease-out infinite",
      ].join(";"),
    );
    wrap.append(pulse);
  }
  const dot = document.createElement("span");
  dot.setAttribute(
    "style",
    [
      "position:absolute",
      "inset:0",
      "border-radius:999px",
      `background:${colorVar}`,
      "border:2px solid var(--color-bg-white)",
      "box-shadow:0 1px 4px rgba(0,0,0,0.25)",
    ].join(";"),
  );
  wrap.append(dot);
  return wrap;
}

interface LegendDotProps {
  kind: ItemKind;
}

function LegendDot({ kind }: LegendDotProps): JSX.Element {
  const bg =
    kind === "venue"
      ? "bg-green-primary"
      : kind === "tip"
        ? "bg-blush-accent"
        : "bg-navy-dark";
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full border border-bg-white ${bg}`}
    />
  );
}

/**
 * Format a walking distance for the list panel.
 *
 * @param meters - Distance in meters.
 * @returns Short display string, e.g. ``"120 m"`` or ``"1.2 km"``.
 */
function formatDistance(meters: number): string {
  if (meters < 100) return `${meters} m`;
  if (meters < 1000) return `${Math.round(meters / 10) * 10} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}
