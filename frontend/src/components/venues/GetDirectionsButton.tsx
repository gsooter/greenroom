"use client";

/**
 * Client-only "Get directions" button.
 *
 * SSR renders a safe Google Maps link so the button has a working href
 * immediately and search engines see something sensible. After hydration
 * we inspect `navigator.userAgent`; iPhone, iPad, and macOS users get
 * upgraded to an `https://maps.apple.com/...` URL, which the OS hands
 * directly to the Apple Maps app.
 */

import { useEffect, useMemo, useState } from "react";

import type { MapProvider } from "@/lib/maps";
import { buildDirectionsUrl, detectMapProvider } from "@/lib/maps";

interface GetDirectionsButtonProps {
  venueName: string;
  latitude: number;
  longitude: number;
  address?: string | null;
  className?: string;
}

const DEFAULT_CLASSNAME =
  "inline-flex w-fit items-center gap-1.5 rounded-md bg-green-primary px-3 py-1.5 text-sm font-medium text-text-inverse hover:bg-green-dark";

/**
 * Renders a map deep-link as an `<a>` sized for use in venue headers.
 *
 * @param venueName - Display name shown in the pin callout.
 * @param latitude - WGS-84 latitude.
 * @param longitude - WGS-84 longitude.
 * @param address - Optional street address appended to the pin label.
 * @param className - Optional Tailwind class overrides for the button.
 */
export default function GetDirectionsButton({
  venueName,
  latitude,
  longitude,
  address,
  className,
}: GetDirectionsButtonProps): JSX.Element {
  const [provider, setProvider] = useState<MapProvider>("google");

  useEffect(() => {
    setProvider(detectMapProvider());
  }, []);

  const href = useMemo(
    () =>
      buildDirectionsUrl(provider, { venueName, latitude, longitude, address }),
    [provider, venueName, latitude, longitude, address],
  );

  const label =
    provider === "apple" ? "Get directions · Apple Maps" : "Get directions · Google Maps";

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className ?? DEFAULT_CLASSNAME}
      aria-label={label}
    >
      <span aria-hidden="true">↗</span>
      Get directions
    </a>
  );
}
