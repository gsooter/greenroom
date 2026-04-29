/**
 * City picker dropdown — client component.
 *
 * Writes the selected city slug into the URL as `?city=<slug>` (or
 * clears it for "All cities") and leaves the rest of the search params
 * intact. Public browse pages read the `city` param on the server and
 * narrow their API calls accordingly. Cities are grouped by region
 * (DMV, Baltimore, RVA) via `<optgroup>` so users can tell them apart.
 */

"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useId, useMemo } from "react";

import type { City } from "@/types";

interface CityPickerProps {
  cities: City[];
}

const REGION_ORDER = ["DMV", "Baltimore", "RVA"] as const;
const REGION_LABELS: Record<string, string> = {
  DMV: "DMV — DC, Maryland & Northern Virginia",
  Baltimore: "Baltimore",
  RVA: "Richmond",
};

interface RegionGroup {
  region: string;
  label: string;
  cities: City[];
}

function groupCitiesByRegion(cities: City[]): RegionGroup[] {
  const groups = new Map<string, City[]>();
  for (const city of cities) {
    const list = groups.get(city.region) ?? [];
    list.push(city);
    groups.set(city.region, list);
  }
  const ordered: RegionGroup[] = [];
  for (const region of REGION_ORDER) {
    const members = groups.get(region);
    if (members && members.length) {
      ordered.push({
        region,
        label: REGION_LABELS[region] ?? region,
        cities: members,
      });
      groups.delete(region);
    }
  }
  for (const [region, members] of Array.from(groups.entries())) {
    ordered.push({
      region,
      label: REGION_LABELS[region] ?? region,
      cities: members,
    });
  }
  return ordered;
}

export default function CityPicker({ cities }: CityPickerProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectId = useId();
  const selectedSlug = searchParams.get("city");
  const regionGroups = useMemo(() => groupCitiesByRegion(cities), [cities]);

  const onChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const value = event.target.value;
      const params = new URLSearchParams(searchParams.toString());
      if (value === "") {
        params.delete("city");
      } else {
        params.set("city", value);
      }
      params.delete("page");
      const query = params.toString();
      router.push(query ? `${pathname}?${query}` : pathname);
    },
    [pathname, router, searchParams],
  );

  return (
    <label
      htmlFor={selectId}
      className="relative flex items-center gap-2 text-sm"
    >
      <span className="sr-only">City</span>
      <span
        aria-hidden
        className="pointer-events-none absolute left-3 flex h-4 w-4 items-center justify-center text-text-secondary"
      >
        <svg
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4 w-4"
        >
          <path d="M8 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.3 4.5 8.5 4.5 8.5s4.5-5.2 4.5-8.5c0-2.5-2-4.5-4.5-4.5Z" />
          <circle cx="8" cy="6" r="1.75" />
        </svg>
      </span>
      <select
        id={selectId}
        value={selectedSlug ?? ""}
        onChange={onChange}
        className="appearance-none rounded-full border border-border bg-bg-white py-1.5 pl-9 pr-9 text-sm font-medium text-text-primary shadow-sm transition hover:border-green-primary focus:border-green-primary focus:outline-none focus:ring-2 focus:ring-green-soft"
      >
        <option value="">All cities (DMV default)</option>
        {regionGroups.map((group) => (
          <optgroup key={group.region} label={group.label}>
            {group.cities.map((city) => (
              <option key={city.id} value={city.slug}>
                {city.name}, {city.state}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <span
        aria-hidden
        className="pointer-events-none absolute right-3 flex h-4 w-4 items-center justify-center text-text-secondary"
      >
        <svg
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-3 w-3"
        >
          <path d="M3 6l5 5 5-5" />
        </svg>
      </span>
    </label>
  );
}
