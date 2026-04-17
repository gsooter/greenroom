/**
 * Small pill that labels a city's region or state.
 *
 * Used on event and venue cards so browsers can orient themselves within
 * the DMV fleet at a glance.
 */

import type { NestedCity } from "@/types";

interface RegionBadgeProps {
  city: NestedCity | null;
  className?: string;
}

export default function RegionBadge({ city, className = "" }: RegionBadgeProps) {
  if (!city) return null;
  const label = `${city.name}, ${city.state}`;
  return (
    <span
      className={
        "inline-flex items-center rounded-full border border-border bg-surface/70 " +
        "px-2 py-0.5 text-xs font-medium text-muted " +
        className
      }
    >
      {label}
    </span>
  );
}
