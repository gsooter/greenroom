/**
 * Static Apple Maps snapshot for a venue.
 *
 * Server-rendered: the URL is signed by the backend and cached for 24
 * hours, so the first paint of every venue page already includes the
 * map without any client-side JS. We wrap the image in a link to the
 * Get Directions flow so the whole card is tappable.
 *
 * If the backend replies with 503 (credentials not configured) or 404
 * (no coordinates) the component renders nothing — the venue detail
 * page remains usable without a map.
 */

import { config } from "@/lib/config";

interface SnapshotResponse {
  data: {
    url: string;
    width: number;
    height: number;
  };
}

interface VenueMapSnapshotProps {
  slug: string;
  venueName: string;
  width?: number;
  height?: number;
  scheme?: "light" | "dark";
}

/**
 * Fetches a signed snapshot URL from the backend. Returns null on any
 * non-OK response so the caller can render a placeholder-free layout
 * when the map is unavailable.
 */
async function fetchSnapshot(
  slug: string,
  params: URLSearchParams,
): Promise<SnapshotResponse["data"] | null> {
  const url = `${config.apiUrl}/api/v1/venues/${encodeURIComponent(slug)}/map-snapshot?${params.toString()}`;
  const res = await fetch(url, { next: { revalidate: 86400 } });
  if (!res.ok) return null;
  const body = (await res.json()) as SnapshotResponse;
  return body.data;
}

/**
 * Apple Maps static image for a venue.
 *
 * @param slug - Venue slug, used to hit the backend snapshot endpoint.
 * @param venueName - Display name used in the alt text and Maps query.
 * @param width - CSS pixel width of the image. Defaults to 600.
 * @param height - CSS pixel height of the image. Defaults to 280.
 * @param scheme - "light" or "dark" Apple map style. Defaults to "light".
 */
export default async function VenueMapSnapshot({
  slug,
  venueName,
  width = 600,
  height = 280,
  scheme = "light",
}: VenueMapSnapshotProps): Promise<JSX.Element | null> {
  const params = new URLSearchParams({
    width: String(width),
    height: String(height),
    scheme,
  });
  const snapshot = await fetchSnapshot(slug, params);
  if (!snapshot) return null;

  const directionsHref = `https://maps.apple.com/?q=${encodeURIComponent(venueName)}`;

  return (
    <a
      href={directionsHref}
      target="_blank"
      rel="noopener noreferrer"
      className="group block overflow-hidden rounded-lg border border-border bg-bg-surface"
      aria-label={`Open ${venueName} in Apple Maps`}
    >
      <img
        src={snapshot.url}
        alt={`Map showing the location of ${venueName}`}
        width={snapshot.width}
        height={snapshot.height}
        className="block h-full w-full object-cover transition-transform group-hover:scale-[1.01]"
        loading="lazy"
        decoding="async"
      />
    </a>
  );
}
