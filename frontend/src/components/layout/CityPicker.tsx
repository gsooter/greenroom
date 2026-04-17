/**
 * City picker dropdown — client component.
 *
 * Writes the selected city slug into the URL as `?city=<slug>` (or
 * clears it for "All DMV cities") and leaves the rest of the search
 * params intact. Public browse pages read the `city` param on the
 * server and narrow their API calls accordingly.
 */

"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useId } from "react";

import type { City } from "@/types";

interface CityPickerProps {
  cities: City[];
  selectedSlug: string | null;
}

export default function CityPicker({ cities, selectedSlug }: CityPickerProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectId = useId();

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
    <label htmlFor={selectId} className="flex items-center gap-2 text-sm">
      <span className="sr-only">City</span>
      <select
        id={selectId}
        value={selectedSlug ?? ""}
        onChange={onChange}
        className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-foreground focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
      >
        <option value="">All DMV cities</option>
        {cities.map((city) => (
          <option key={city.id} value={city.slug}>
            {city.name}, {city.state}
          </option>
        ))}
      </select>
    </label>
  );
}
