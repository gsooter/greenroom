/**
 * Genre filter pills for the Tonight map.
 *
 * Pure-UI client component — owns no data fetching of its own.
 * A parent passes:
 *
 * * ``buckets`` — the six pin-color groups exposed via ``genre-colors``
 *   plus their labels and representative genre-query strings.
 * * ``activeBucket`` — the color key whose genres are currently
 *   forwarded to ``/maps/tonight`` as the ``genres`` filter.
 * * ``onChange`` — called with the new bucket key (or null for "all").
 *
 * Each pill is painted with the bucket's pin color so the legend and
 * the map pins stay visually aligned.
 */

"use client";

import { pinColorStyle, type MapPinColor } from "@/lib/genre-colors";

export interface GenreBucket {
  key: MapPinColor;
  label: string;
  genres: readonly string[];
}

export const TONIGHT_GENRE_BUCKETS: readonly GenreBucket[] = [
  {
    key: "green",
    label: "Indie / Rock",
    genres: ["indie", "indie rock", "rock", "alternative", "punk", "post-punk", "emo"],
  },
  {
    key: "blush",
    label: "Pop / Folk",
    genres: ["pop", "folk", "singer-songwriter", "americana", "country"],
  },
  {
    key: "amber",
    label: "Electronic",
    genres: ["electronic", "dance", "house", "techno", "edm", "dj"],
  },
  {
    key: "coral",
    label: "Hip-Hop",
    genres: ["hip-hop", "hip hop", "rap", "trap"],
  },
  {
    key: "gold",
    label: "Jazz / Soul",
    genres: ["jazz", "soul", "r&b", "rnb", "funk", "blues"],
  },
];

interface FilterBarProps {
  activeBucket: MapPinColor | null;
  onChange: (bucket: MapPinColor | null) => void;
  counts?: Partial<Record<MapPinColor, number>> & { total?: number };
}

/**
 * Horizontal scroller of genre-bucket pills.
 *
 * The "All" pill clears the filter. Each bucket pill shows its color
 * swatch and (optionally) a count of tonight's events in that bucket.
 *
 * @param activeBucket - Currently selected pin-color bucket, or null.
 * @param onChange - Callback fired with the new bucket or null.
 * @param counts - Optional per-bucket counts rendered next to each pill.
 */
export default function FilterBar({
  activeBucket,
  onChange,
  counts,
}: FilterBarProps): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label="Filter tonight's pins by genre"
      className="flex flex-wrap items-center gap-2"
    >
      <FilterPill
        label="All"
        count={counts?.total}
        active={activeBucket === null}
        onSelect={() => onChange(null)}
      />
      {TONIGHT_GENRE_BUCKETS.map((bucket) => (
        <FilterPill
          key={bucket.key}
          label={bucket.label}
          count={counts?.[bucket.key]}
          active={activeBucket === bucket.key}
          swatchColor={bucket.key}
          onSelect={() => onChange(bucket.key)}
        />
      ))}
    </div>
  );
}

interface FilterPillProps {
  label: string;
  count?: number;
  active: boolean;
  swatchColor?: MapPinColor;
  onSelect: () => void;
}

/**
 * Single pill button used inside {@link FilterBar}.
 *
 * The swatch (when present) exposes the pin-color CSS variable via the
 * ``--pin-color`` custom property so the dot stays single-sourced in
 * ``globals.css``.
 */
function FilterPill({
  label,
  count,
  active,
  swatchColor,
  onSelect,
}: FilterPillProps): JSX.Element {
  const base =
    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition";
  const activeClass = "border-green-primary bg-green-primary text-text-inverse";
  const idleClass =
    "border-border bg-bg-white text-text-primary hover:border-green-primary";

  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onSelect}
      className={`${base} ${active ? activeClass : idleClass}`}
    >
      {swatchColor ? (
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{
            ...pinColorStyle(swatchColor),
            backgroundColor: "var(--pin-color)",
          }}
        />
      ) : null}
      <span>{label}</span>
      {typeof count === "number" ? (
        <span
          className={
            active ? "text-text-inverse/80" : "text-text-secondary"
          }
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

/**
 * Look up the representative genre-query list for a bucket key.
 *
 * Used by the map page to convert the active bucket into the
 * ``genres=`` query parameter sent to ``/maps/tonight``.
 *
 * @param bucket - Bucket color key, or null for "no filter".
 * @returns The genre-name list, or ``undefined`` when bucket is null.
 */
export function genresForBucket(
  bucket: MapPinColor | null,
): readonly string[] | undefined {
  if (bucket === null) return undefined;
  const match = TONIGHT_GENRE_BUCKETS.find((b) => b.key === bucket);
  return match?.genres;
}
