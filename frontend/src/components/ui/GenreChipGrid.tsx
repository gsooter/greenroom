/**
 * Reusable genre selector rendered as a wrap-flow grid of toggleable
 * pill chips. Used by onboarding (TasteStep) and the settings page so
 * both surfaces stay visually identical and pick up palette tweaks in
 * one place.
 */

"use client";

import type { Genre } from "@/types";

interface Props {
  genres: Genre[];
  selected: Set<string>;
  onToggle: (slug: string) => void;
  disabled?: boolean;
}

/**
 * Render a flex-wrap grid of genre pills, each toggleable.
 *
 * The component is uncontrolled with respect to API persistence — it
 * just reports toggles upward. Callers decide whether to PATCH on every
 * click or batch the diff.
 */
export function GenreChipGrid({
  genres,
  selected,
  onToggle,
  disabled = false,
}: Props): JSX.Element {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Genres">
      {genres.map((g) => {
        const active = selected.has(g.slug);
        return (
          <button
            key={g.slug}
            type="button"
            onClick={() => onToggle(g.slug)}
            disabled={disabled}
            className={
              active
                ? "rounded-full bg-green-soft px-3 py-1.5 text-xs font-medium text-green-dark ring-1 ring-green-primary disabled:cursor-not-allowed disabled:opacity-60"
                : "rounded-full bg-bg-surface px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-green-soft/60 disabled:cursor-not-allowed disabled:opacity-60"
            }
            aria-pressed={active}
          >
            <span aria-hidden className="mr-1">
              {g.emoji}
            </span>
            {g.label}
          </button>
        );
      })}
    </div>
  );
}
