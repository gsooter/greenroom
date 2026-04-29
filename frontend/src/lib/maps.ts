/**
 * Deep-link helpers for native map apps.
 *
 * These are pure string builders — the caller decides which provider
 * to render based on platform detection (or by rendering both links).
 */

export type MapProvider = "apple" | "google";

interface DirectionsInput {
  latitude: number;
  longitude: number;
  venueName: string;
  address?: string | null;
}

/**
 * Detects which map provider to prefer based on the current user agent.
 * Apple platforms (iOS, macOS) open the native Apple Maps app from an
 * `https://maps.apple.com/...` link; everywhere else gets Google Maps.
 *
 * @returns `"apple"` on iOS/iPadOS/macOS, `"google"` otherwise.
 */
export function detectMapProvider(): MapProvider {
  if (typeof navigator === "undefined") return "google";
  const ua = navigator.userAgent;
  // iPad Pro with desktop UA reports "Macintosh" — covered by "Mac OS X".
  return /iPhone|iPad|iPod|Mac OS X|Macintosh/.test(ua) ? "apple" : "google";
}

/**
 * Builds a Get Directions URL for the given provider and destination.
 *
 * The destination pin is labeled with the venue name (and address if
 * provided) so the map opens with a legible callout instead of a raw
 * lat/lng tuple. The user's current location is inferred client-side
 * by the map app — no origin is encoded.
 *
 * @param provider - Target app family.
 * @param input - Destination lat/lng plus display metadata.
 * @returns A fully-qualified HTTPS URL safe to place in `<a href>`.
 */
export function buildDirectionsUrl(
  provider: MapProvider,
  input: DirectionsInput,
): string {
  const dest = `${input.latitude.toFixed(6)},${input.longitude.toFixed(6)}`;
  const labelParts = [input.venueName];
  if (input.address) labelParts.push(input.address);
  const label = encodeURIComponent(labelParts.join(", "));

  if (provider === "apple") {
    return `https://maps.apple.com/?daddr=${dest}&q=${label}`;
  }
  return `https://www.google.com/maps/dir/?api=1&destination=${dest}&destination_place_id=${label}`;
}
