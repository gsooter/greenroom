/**
 * MapKit JS loader and a minimal typed surface for the Tonight map.
 *
 * We only expose the bits of MapKit JS that the Tonight and Near-Me
 * surfaces actually use — `Map`, `Coordinate`, `CoordinateRegion`,
 * `CoordinateSpan`, and `Annotation`. That keeps the global typing
 * contained to this module so components don't have to reach into
 * `window.mapkit` themselves.
 *
 * The script is loaded lazily on first call to {@link loadMapKit} so
 * pages that don't embed a map don't pay the network cost. Subsequent
 * calls resolve the same promise.
 */

const MAPKIT_SRC = "https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.js";

export interface MapKitCoordinate {
  latitude: number;
  longitude: number;
}

export interface MapKitCoordinateSpan {
  latitudeDelta: number;
  longitudeDelta: number;
}

export interface MapKitCoordinateRegion {
  center: MapKitCoordinate;
  span: MapKitCoordinateSpan;
}

export interface MapKitAnnotationOptions {
  title?: string;
  subtitle?: string;
  color?: string;
  glyphText?: string;
  glyphColor?: string;
  selected?: boolean;
  data?: Record<string, unknown>;
}

export interface MapKitAnnotation {
  coordinate: MapKitCoordinate;
  title: string;
  subtitle: string;
  color: string;
  glyphText: string;
  glyphColor: string;
  selected: boolean;
  data: Record<string, unknown>;
  addEventListener(
    name: "select" | "deselect",
    handler: (event: { target: MapKitAnnotation }) => void,
  ): void;
  removeEventListener(
    name: "select" | "deselect",
    handler: (event: { target: MapKitAnnotation }) => void,
  ): void;
}

export interface MapKitMap {
  region: MapKitCoordinateRegion;
  showsCompass: string;
  showsZoomControl: boolean;
  showsMapTypeControl: boolean;
  showsUserLocationControl: boolean;
  colorScheme: string;
  addAnnotation(annotation: MapKitAnnotation): void;
  addAnnotations(annotations: MapKitAnnotation[]): void;
  removeAnnotations(annotations: MapKitAnnotation[]): void;
  destroy(): void;
}

export interface MapKitStatic {
  init(options: {
    authorizationCallback: (done: (token: string) => void) => void;
    language?: string;
  }): void;
  Map: new (element: HTMLElement, options?: Record<string, unknown>) => MapKitMap;
  Coordinate: new (latitude: number, longitude: number) => MapKitCoordinate;
  CoordinateSpan: new (
    latitudeDelta: number,
    longitudeDelta: number,
  ) => MapKitCoordinateSpan;
  CoordinateRegion: new (
    center: MapKitCoordinate,
    span: MapKitCoordinateSpan,
  ) => MapKitCoordinateRegion;
  Annotation: new (
    coordinate: MapKitCoordinate,
    factory: () => HTMLElement,
    options?: MapKitAnnotationOptions,
  ) => MapKitAnnotation;
}

declare global {
  interface Window {
    mapkit?: MapKitStatic;
  }
}

let loadPromise: Promise<MapKitStatic> | null = null;

/**
 * Inject the MapKit JS script tag and resolve once `window.mapkit` is
 * available. Idempotent — subsequent calls return the same promise.
 *
 * @returns A promise that resolves to the `mapkit` global once ready.
 */
export function loadMapKit(): Promise<MapKitStatic> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("MapKit JS is only available in the browser."));
  }
  if (window.mapkit) {
    return Promise.resolve(window.mapkit);
  }
  if (loadPromise) {
    return loadPromise;
  }

  loadPromise = new Promise<MapKitStatic>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${MAPKIT_SRC}"]`,
    );
    let settled = false;
    const finish = (fn: () => void): void => {
      if (settled) return;
      settled = true;
      fn();
    };
    const onReady = (): void => {
      if (window.mapkit) {
        finish(() => resolve(window.mapkit as MapKitStatic));
      } else {
        finish(() =>
          reject(new Error("MapKit script loaded but window.mapkit is missing.")),
        );
      }
    };

    if (!existing) {
      const script = document.createElement("script");
      script.src = MAPKIT_SRC;
      script.crossOrigin = "anonymous";
      script.async = true;
      script.onload = onReady;
      script.onerror = () =>
        finish(() =>
          reject(
            new Error(
              "Failed to load mapkit.js — check the browser console for CSP or network errors.",
            ),
          ),
        );
      document.head.appendChild(script);
    } else {
      existing.addEventListener("load", onReady, { once: true });
      if (window.mapkit) onReady();
    }
  });
  return loadPromise;
}

/**
 * Initialize MapKit JS with a fetcher that resupplies tokens on demand.
 *
 * MapKit calls the authorization callback whenever it needs a fresh
 * token, which can happen multiple times across the life of the page
 * (for example, after a token expires). The fetcher is therefore called
 * each time rather than just once at init.
 *
 * @param tokenFetcher - Returns a signed developer token string.
 */
export async function initMapKit(
  tokenFetcher: () => Promise<string>,
): Promise<MapKitStatic> {
  const mk = await loadMapKit();
  mk.init({
    authorizationCallback: (done) => {
      tokenFetcher()
        .then(done)
        .catch(() => done(""));
    },
  });
  return mk;
}
