/**
 * Shows Near Me — client shell orchestrating geolocation, fetch, and UI toggles.
 *
 * The user's browser supplies the current position via the Geolocation
 * API. On success the shell calls `/api/v1/maps/near-me` with a radius
 * and a time window, then renders the result as either the MapKit JS
 * map (reusing {@link TonightMap}) or a flat list. A "Surprise Me"
 * button picks a random row and routes the user to that event page —
 * the primary flow this surface exists to support.
 *
 * Permission states:
 *   - `idle`      — geolocation hasn't been requested yet.
 *   - `pending`   — waiting on the browser permission prompt or the fix.
 *   - `granted`   — have coordinates; may still be fetching events.
 *   - `denied`    — user rejected or the browser blocked the prompt.
 *   - `unsupported` — no Geolocation API (SSR-time or rare browsers).
 */

"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import TonightMap from "@/components/map/TonightMap";
import { getNearMeEvents } from "@/lib/api/maps";
import { formatDistance as formatDistancePref, useDistanceUnit } from "@/lib/preferences";
import type { NearMeEvent, NearMeWindow } from "@/types";

type PermissionState =
  | "idle"
  | "pending"
  | "granted"
  | "denied"
  | "unsupported";

type ViewMode = "map" | "list";

interface Coordinates {
  latitude: number;
  longitude: number;
}

interface NearMeShellProps {
  defaultRadiusKm?: number;
  defaultWindow?: NearMeWindow;
}

// Radius chip options per display unit. Internally we store the selected
// radius in kilometers (the backend expects km), but the chip labels use
// whole numbers in whichever unit the user has chosen so "5 mi" vs
// "3.1 mi" doesn't surface in the control.
const RADIUS_OPTIONS_KM: readonly number[] = [2, 5, 10, 25];
const RADIUS_OPTIONS_MI: readonly number[] = [1, 3, 5, 10, 25];
const KM_PER_MILE = 1.609344;
const WINDOW_OPTIONS: readonly { value: NearMeWindow; label: string }[] = [
  { value: "tonight", label: "Tonight" },
  { value: "week", label: "This week" },
];

// If the user is granting location from well outside the DMV (e.g. they
// travel with the app open), a local radius returns nothing useful. Rather
// than an empty surface, we fall back to the same query the /map page
// uses — every DMV pin tonight/this week — while still centering the map
// on the user's own coordinates for context.
const DC_CENTER = { latitude: 38.9072, longitude: -77.0369 };
const OUT_OF_REGION_THRESHOLD_KM = 100;
const OUT_OF_REGION_RADIUS_KM = 500;

function haversineKm(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const R = 6371;
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const sinLat = Math.sin(dLat / 2);
  const sinLon = Math.sin(dLon / 2);
  const h = sinLat * sinLat + Math.cos(lat1) * Math.cos(lat2) * sinLon * sinLon;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/**
 * Render the Near Me surface: permission gate, filters, view toggle, results.
 *
 * @param defaultRadiusKm - Initial radius selection in km. Default 10.
 * @param defaultWindow - Initial time window. Default "tonight".
 */
export default function NearMeShell({
  defaultRadiusKm = 10,
  defaultWindow = "tonight",
}: NearMeShellProps): JSX.Element {
  const [permission, setPermission] = useState<PermissionState>("idle");
  const [coords, setCoords] = useState<Coordinates | null>(null);
  const [coordsError, setCoordsError] = useState<string | null>(null);
  const [events, setEvents] = useState<NearMeEvent[]>([]);
  const [isFetching, setIsFetching] = useState<boolean>(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [radiusKm, setRadiusKm] = useState<number>(defaultRadiusKm);
  const [timeWindow, setTimeWindow] = useState<NearMeWindow>(defaultWindow);
  const [view, setView] = useState<ViewMode>("map");
  const [unit] = useDistanceUnit();
  const router = useRouter();

  // When the user's unit preference changes, snap the radius to the
  // closest available chip in the new unit so the control always
  // reflects an active selection.
  useEffect(() => {
    const options = unit === "mi" ? RADIUS_OPTIONS_MI : RADIUS_OPTIONS_KM;
    const currentInUnit = unit === "mi" ? radiusKm / KM_PER_MILE : radiusKm;
    const closest = options.reduce(
      (best, raw) =>
        Math.abs(currentInUnit - raw) < Math.abs(currentInUnit - best)
          ? raw
          : best,
      options[0] ?? 5,
    );
    const snappedKm = unit === "mi" ? closest * KM_PER_MILE : closest;
    if (Math.abs(snappedKm - radiusKm) > 0.01) {
      setRadiusKm(snappedKm);
    }
    // Intentionally leave radiusKm out of the dep list — we only want
    // this to run when the unit flips, not on every radius change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unit]);

  const isOutOfRegion = Boolean(
    coords && haversineKm(coords, DC_CENTER) > OUT_OF_REGION_THRESHOLD_KM,
  );
  const effectiveRadiusKm = isOutOfRegion ? OUT_OF_REGION_RADIUS_KM : radiusKm;
  // Memoize so the object identity doesn't change on every render — the
  // fetch effect keys off this and would loop otherwise.
  const effectiveCenter = useMemo<Coordinates | null>(() => {
    if (!coords) return null;
    if (isOutOfRegion) return DC_CENTER;
    return coords;
  }, [coords, isOutOfRegion]);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setPermission("unsupported");
    }
  }, []);

  const requestLocation = useCallback((): void => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setPermission("unsupported");
      return;
    }
    setPermission("pending");
    setCoordsError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setCoords({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        setPermission("granted");
      },
      (error) => {
        setPermission("denied");
        setCoordsError(describeGeolocationError(error));
      },
      {
        enableHighAccuracy: false,
        maximumAge: 60_000,
        timeout: 10_000,
      },
    );
  }, []);

  useEffect(() => {
    if (!effectiveCenter) return;
    let cancelled = false;
    setIsFetching(true);
    setFetchError(null);
    getNearMeEvents({
      latitude: effectiveCenter.latitude,
      longitude: effectiveCenter.longitude,
      radiusKm: effectiveRadiusKm,
      window: timeWindow,
      limit: 100,
    })
      .then((envelope) => {
        if (cancelled) return;
        setEvents(envelope.data);
      })
      .catch(() => {
        if (cancelled) return;
        setEvents([]);
        setFetchError(
          "We couldn't load nearby shows. Try again in a moment.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveCenter, effectiveRadiusKm, timeWindow]);

  const onSurprise = useCallback((): void => {
    if (events.length === 0) return;
    const index = Math.floor(Math.random() * events.length);
    const target = events[index];
    if (!target) return;
    router.push(`/events/${target.slug}`);
  }, [events, router]);

  if (permission === "idle") {
    return <PermissionPrompt onRequest={requestLocation} />;
  }
  if (permission === "pending") {
    return <PermissionPrompt onRequest={requestLocation} pending />;
  }
  if (permission === "unsupported") {
    return (
      <FallbackMessage
        heading="Location isn't available in this browser"
        body="Try a recent version of Chrome, Safari, or Firefox — or browse the full DMV map instead."
        ctaHref="/map"
        ctaLabel="Open tonight's DC map"
      />
    );
  }
  if (permission === "denied") {
    return (
      <FallbackMessage
        heading="Location permission denied"
        body={
          coordsError ??
          "Greenroom needs your location to show nearby shows. Enable location access in your browser and try again."
        }
        ctaHref="/map"
        ctaLabel="Open tonight's DC map"
        onRetry={requestLocation}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <FiltersRow
        radiusKm={radiusKm}
        window={timeWindow}
        view={view}
        unit={unit}
        onRadiusChange={setRadiusKm}
        onWindowChange={setTimeWindow}
        onViewChange={setView}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {isFetching
            ? "Finding shows near you…"
            : describeResultCount(
                events.length,
                effectiveRadiusKm,
                timeWindow,
                unit,
                isOutOfRegion,
              )}
        </p>
        <SurpriseButton
          disabled={events.length === 0 || isFetching}
          onClick={onSurprise}
        />
      </div>

      {isOutOfRegion ? (
        <div className="rounded-lg border border-border bg-bg-surface px-4 py-3 text-xs text-text-secondary">
          Looks like you&apos;re outside the DMV tonight — we&apos;re showing
          every DC-area show so you can still plan ahead. Your radius filter
          kicks back in when you&apos;re closer to town.
        </div>
      ) : null}

      {fetchError ? (
        <div className="rounded-lg border border-blush-accent/40 bg-blush-soft/60 px-4 py-3 text-sm text-text-primary">
          {fetchError}
        </div>
      ) : null}

      {view === "map" ? (
        <TonightMap events={events} />
      ) : (
        <NearMeList events={events} unit={unit} />
      )}
    </div>
  );
}

interface PermissionPromptProps {
  onRequest: () => void;
  pending?: boolean;
}

function PermissionPrompt({
  onRequest,
  pending = false,
}: PermissionPromptProps): JSX.Element {
  return (
    <div className="flex flex-col items-start gap-4 rounded-lg border border-border bg-bg-surface p-6">
      <div className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold text-text-primary">
          Find shows near you
        </h2>
        <p className="text-sm text-text-secondary">
          Share your location and we&apos;ll show DMV concerts within a few
          miles, sorted nearest-first. We don&apos;t store your coordinates —
          they stay in your browser for the length of this visit.
        </p>
      </div>
      <button
        type="button"
        onClick={onRequest}
        disabled={pending}
        className="rounded-md bg-green-primary px-4 py-2 text-sm font-semibold text-text-inverse shadow-sm hover:bg-green-dark disabled:opacity-60"
      >
        {pending ? "Requesting location…" : "Use my location"}
      </button>
    </div>
  );
}

interface FallbackMessageProps {
  heading: string;
  body: string;
  ctaHref: string;
  ctaLabel: string;
  onRetry?: () => void;
}

function FallbackMessage({
  heading,
  body,
  ctaHref,
  ctaLabel,
  onRetry,
}: FallbackMessageProps): JSX.Element {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-bg-surface p-6">
      <h2 className="text-lg font-semibold text-text-primary">{heading}</h2>
      <p className="text-sm text-text-secondary">{body}</p>
      <div className="flex flex-wrap gap-2">
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-border bg-bg-white px-3 py-2 text-sm font-medium text-text-primary hover:border-green-primary hover:text-accent"
          >
            Try again
          </button>
        ) : null}
        <a
          href={ctaHref}
          className="rounded-md bg-green-primary px-3 py-2 text-sm font-semibold text-text-inverse hover:bg-green-dark"
        >
          {ctaLabel}
        </a>
      </div>
    </div>
  );
}

interface FiltersRowProps {
  radiusKm: number;
  window: NearMeWindow;
  view: ViewMode;
  unit: "mi" | "km";
  onRadiusChange: (km: number) => void;
  onWindowChange: (w: NearMeWindow) => void;
  onViewChange: (v: ViewMode) => void;
}

function FiltersRow({
  radiusKm,
  window,
  view,
  unit,
  onRadiusChange,
  onWindowChange,
  onViewChange,
}: FiltersRowProps): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <fieldset className="flex items-center gap-1 rounded-full border border-border bg-bg-surface p-1">
        <legend className="sr-only">Radius</legend>
        {(unit === "mi" ? RADIUS_OPTIONS_MI : RADIUS_OPTIONS_KM).map((raw) => {
          const asKm = unit === "mi" ? raw * KM_PER_MILE : raw;
          const active = Math.abs(radiusKm - asKm) < 0.01;
          return (
            <button
              key={raw}
              type="button"
              aria-pressed={active}
              onClick={() => onRadiusChange(asKm)}
              className={
                "rounded-full px-3 py-1 text-xs font-medium transition " +
                (active
                  ? "bg-green-primary text-text-inverse"
                  : "text-text-secondary hover:text-text-primary")
              }
            >
              {raw} {unit}
            </button>
          );
        })}
      </fieldset>

      <fieldset className="flex items-center gap-1 rounded-full border border-border bg-bg-surface p-1">
        <legend className="sr-only">Time window</legend>
        {WINDOW_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            aria-pressed={window === opt.value}
            onClick={() => onWindowChange(opt.value)}
            className={
              "rounded-full px-3 py-1 text-xs font-medium transition " +
              (window === opt.value
                ? "bg-green-primary text-text-inverse"
                : "text-text-secondary hover:text-text-primary")
            }
          >
            {opt.label}
          </button>
        ))}
      </fieldset>

      <fieldset
        className="ml-auto flex items-center gap-1 rounded-full border border-border bg-bg-surface p-1"
        aria-label="View mode"
      >
        <legend className="sr-only">View mode</legend>
        <button
          type="button"
          aria-pressed={view === "map"}
          onClick={() => onViewChange("map")}
          className={
            "rounded-full px-3 py-1 text-xs font-medium transition " +
            (view === "map"
              ? "bg-green-primary text-text-inverse"
              : "text-text-secondary hover:text-text-primary")
          }
        >
          Map
        </button>
        <button
          type="button"
          aria-pressed={view === "list"}
          onClick={() => onViewChange("list")}
          className={
            "rounded-full px-3 py-1 text-xs font-medium transition " +
            (view === "list"
              ? "bg-green-primary text-text-inverse"
              : "text-text-secondary hover:text-text-primary")
          }
        >
          List
        </button>
      </fieldset>
    </div>
  );
}

interface SurpriseButtonProps {
  disabled: boolean;
  onClick: () => void;
}

function SurpriseButton({
  disabled,
  onClick,
}: SurpriseButtonProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-full bg-blush-accent px-4 py-2 text-sm font-semibold text-text-inverse shadow-sm hover:brightness-110 disabled:opacity-60"
    >
      Surprise me
    </button>
  );
}

function NearMeList({
  events,
  unit,
}: {
  events: NearMeEvent[];
  unit: "mi" | "km";
}): JSX.Element {
  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-surface p-6 text-center text-sm text-text-secondary">
        No shows match your filters — try a bigger radius or a wider window.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {events.map((event) => (
        <li
          key={event.id}
          className="flex items-start justify-between gap-3 rounded-lg border border-border bg-bg-white px-4 py-3"
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
              {event.artists.length > 0
                ? ` · ${event.artists.slice(0, 2).join(", ")}`
                : ""}
            </span>
          </div>
          <span className="shrink-0 rounded-full bg-bg-surface px-2 py-1 text-xs font-medium text-text-secondary">
            {formatDistancePref(event.distance_km, unit)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function describeGeolocationError(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) {
    return "Location permission was denied. Enable it for this site in your browser settings and try again.";
  }
  if (error.code === error.POSITION_UNAVAILABLE) {
    return "We couldn't determine your location right now. Try again in a moment.";
  }
  if (error.code === error.TIMEOUT) {
    return "Locating took too long. Try again in a moment.";
  }
  return "Something went wrong reading your location. Try again.";
}

function describeResultCount(
  count: number,
  radiusKm: number,
  window: NearMeWindow,
  unit: "mi" | "km",
  isOutOfRegion: boolean,
): string {
  const scope = window === "tonight" ? "tonight" : "this week";
  if (isOutOfRegion) {
    if (count === 0) return `No DMV shows ${scope} yet.`;
    if (count === 1) return `1 DMV show ${scope}.`;
    return `${count} DMV shows ${scope}.`;
  }
  const radius = formatDistancePref(radiusKm, unit);
  if (count === 0) {
    return `No shows within ${radius} ${scope} yet.`;
  }
  if (count === 1) {
    return `1 show within ${radius} ${scope}.`;
  }
  return `${count} shows within ${radius} ${scope}.`;
}
