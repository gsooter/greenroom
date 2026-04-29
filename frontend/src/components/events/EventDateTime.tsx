/**
 * Client-side event date/time display that honors the user's timezone
 * preference.
 *
 * Rendering strategy: SSR and the initial client render use the default
 * ET zone so hydration lines up exactly. A `useEffect` sets a `mounted`
 * flag after the first paint, and the preference hook then drives a
 * re-render if the user has chosen a non-default zone. This produces a
 * one-tick flicker for out-of-ET users, which we accept in exchange for
 * matching the server render.
 */

"use client";

import { useEffect, useState } from "react";

import { formatEventDate, formatEventTime } from "@/lib/format";
import { DEFAULT_TIMEZONE, useTimezonePreference } from "@/lib/preferences";

interface EventDateTimeProps {
  iso: string | null;
  className?: string;
  fallback?: string;
}

/**
 * Render an event date — and, when the ISO includes a time component, the
 * time — in the user's preferred timezone. Renders a fallback label for
 * missing inputs so callers don't have to branch themselves.
 */
export default function EventDateTime({
  iso,
  className,
  fallback = "Date TBA",
}: EventDateTimeProps): JSX.Element {
  const [tz] = useTimezonePreference();
  const [mounted, setMounted] = useState<boolean>(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!iso) return <span className={className}>{fallback}</span>;
  const effectiveTz = mounted ? tz : DEFAULT_TIMEZONE;
  const date = formatEventDate(iso, effectiveTz);
  const time = formatEventTime(iso, effectiveTz);
  return (
    <span className={className}>
      {date}
      {time ? ` · ${time}` : ""}
    </span>
  );
}
