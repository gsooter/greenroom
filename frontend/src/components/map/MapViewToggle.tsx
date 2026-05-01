/**
 * View switcher for the unified ``/map`` route.
 *
 * The new bottom nav collapses two routes (``/map`` and ``/near-me``)
 * into a single Map tab. The toggle here is the user's surface for
 * choosing between the two views — Tonight (the curated tonight map)
 * vs Near Me (geolocation-driven). Selecting a tab updates the
 * ``view`` query param so the page can be deep-linked or shared.
 */

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export type MapView = "tonight" | "near-me";

const TABS: ReadonlyArray<{ value: MapView; label: string }> = [
  { value: "tonight", label: "Tonight" },
  { value: "near-me", label: "Near Me" },
];

interface Props {
  /** Current active view; controls which pill renders as selected. */
  active: MapView;
}

/**
 * Renders a two-pill segmented control for switching between tonight and
 * near-me views on the unified /map page.
 *
 * Args:
 *     active: The currently active view (``"tonight"`` or ``"near-me"``).
 *
 * Returns:
 *     The segmented toggle wrapped in a labelled container.
 */
export default function MapViewToggle({ active }: Props): JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();

  const onSelect = useCallback(
    (value: MapView): void => {
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (value === "tonight") {
        params.delete("view");
      } else {
        params.set("view", value);
      }
      const qs = params.toString();
      router.replace(qs ? `/map?${qs}` : "/map");
    },
    [router, searchParams],
  );

  return (
    <div
      role="tablist"
      aria-label="Map view"
      className="inline-flex rounded-full border border-border bg-bg-white p-1"
    >
      {TABS.map((tab) => {
        const isActive = tab.value === active;
        return (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(tab.value)}
            className={
              "rounded-full px-4 py-1.5 text-sm font-medium transition " +
              (isActive
                ? "bg-accent text-accent-foreground"
                : "text-text-secondary hover:text-text-primary")
            }
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
