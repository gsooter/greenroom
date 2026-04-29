/**
 * EventFilterPanel — combined trigger button + drawer for /events filters.
 *
 * Client component. Holds local form state seeded from the URL on mount
 * (and reseeded whenever ``initialFilters`` changes — i.e. after a
 * navigation completes). On Apply, ``router.push`` rewrites the URL and
 * the SSR page re-runs with the new filters; on Clear, the same path
 * resets every filter dimension. The panel never directly fetches data —
 * it just owns URL state.
 *
 * Layout: full-height side drawer on md+, bottom sheet (rounded top
 * corners, max 90vh) on mobile. Both share the same scrollable form
 * body and a sticky footer with Apply/Clear actions.
 */

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  EMPTY_FILTERS,
  applyFiltersToParams,
  countActiveFilters,
  isEmptyFilters,
  type EventFilters,
} from "@/lib/event-filters";

export interface FilterPanelGenre {
  slug: string;
  label: string;
}

export interface FilterPanelVenue {
  id: string;
  name: string;
}

interface EventFilterPanelProps {
  /** Filters parsed from the current URL — used to seed the form. */
  initialFilters: EventFilters;
  /** Pre-built params for non-filter context (city, view, window, etc.). */
  baseParams: URLSearchParams;
  /** Genre options for the multi-select. */
  genres: FilterPanelGenre[];
  /** Venue options for the multi-select. */
  venues: FilterPanelVenue[];
  /** Path the panel pushes to. Always ``/events`` today; injectable for tests. */
  pathname?: string;
}

export default function EventFilterPanel({
  initialFilters,
  baseParams,
  genres,
  venues,
  pathname = "/events",
}: EventFilterPanelProps): JSX.Element {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<EventFilters>(initialFilters);

  useEffect(() => {
    setDraft(initialFilters);
  }, [initialFilters]);

  const activeCount = countActiveFilters(initialFilters);

  const navigateTo = (next: EventFilters): void => {
    const params = new URLSearchParams(baseParams);
    applyFiltersToParams(params, next);
    params.delete("page");
    const qs = params.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  };

  const onApply = (): void => {
    setOpen(false);
    navigateTo(draft);
  };

  const onClearAll = (): void => {
    setDraft(EMPTY_FILTERS);
    setOpen(false);
    navigateTo(EMPTY_FILTERS);
  };

  const toggleGenre = (slug: string): void => {
    setDraft((prev) =>
      prev.genres.includes(slug)
        ? { ...prev, genres: prev.genres.filter((g) => g !== slug) }
        : { ...prev, genres: [...prev.genres, slug] },
    );
  };

  const toggleVenue = (id: string): void => {
    setDraft((prev) =>
      prev.venueIds.includes(id)
        ? { ...prev, venueIds: prev.venueIds.filter((v) => v !== id) }
        : { ...prev, venueIds: [...prev.venueIds, id] },
    );
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex items-center gap-2 rounded-full border border-border bg-bg-white px-3 py-1.5 text-sm font-medium text-text-primary shadow-sm transition hover:border-green-primary focus:border-green-primary focus:outline-none focus:ring-2 focus:ring-green-soft"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4 w-4"
        >
          <path d="M2 4h12M4 8h8M6 12h4" />
        </svg>
        <span>Filters</span>
        {activeCount > 0 ? (
          <span
            aria-label={`${activeCount} filters active`}
            className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-green-primary px-1.5 text-xs font-semibold text-text-inverse"
          >
            {activeCount}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="presentation"
          className="fixed inset-0 z-40 flex items-end bg-text-primary/40 md:items-stretch md:justify-end"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Event filters"
            className="flex max-h-[90vh] w-full flex-col overflow-hidden rounded-t-2xl bg-bg-base shadow-lg md:max-h-none md:w-96 md:rounded-none md:rounded-l-2xl"
          >
            <header className="flex items-center justify-between border-b border-border px-5 py-4">
              <h2 className="text-base font-semibold text-text-primary">
                Filters
              </h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close filters"
                className="rounded-full p-1 text-text-secondary transition hover:text-text-primary"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-4 w-4"
                >
                  <path d="M3 3l10 10M13 3L3 13" />
                </svg>
              </button>
            </header>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              <FilterSection title="Date range">
                <div className="flex flex-col gap-2 sm:flex-row">
                  <label className="flex flex-1 flex-col gap-1 text-xs text-text-secondary">
                    From
                    <input
                      type="date"
                      value={draft.dateFrom ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, dateFrom: e.target.value || null })
                      }
                      className="rounded-md border border-border bg-bg-white px-2 py-1.5 text-sm text-text-primary"
                    />
                  </label>
                  <label className="flex flex-1 flex-col gap-1 text-xs text-text-secondary">
                    To
                    <input
                      type="date"
                      value={draft.dateTo ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, dateTo: e.target.value || null })
                      }
                      className="rounded-md border border-border bg-bg-white px-2 py-1.5 text-sm text-text-primary"
                    />
                  </label>
                </div>
              </FilterSection>

              {genres.length > 0 ? (
                <FilterSection title="Genre">
                  <div className="flex flex-wrap gap-2">
                    {genres.map((g) => {
                      const active = draft.genres.includes(g.slug);
                      return (
                        <button
                          key={g.slug}
                          type="button"
                          onClick={() => toggleGenre(g.slug)}
                          aria-pressed={active}
                          className={
                            active
                              ? "rounded-full border border-green-primary bg-green-soft px-3 py-1 text-sm text-text-primary"
                              : "rounded-full border border-border bg-bg-white px-3 py-1 text-sm text-text-primary hover:border-green-primary"
                          }
                        >
                          {g.label}
                        </button>
                      );
                    })}
                  </div>
                </FilterSection>
              ) : null}

              {venues.length > 0 ? (
                <FilterSection title="Venue">
                  <div className="flex max-h-48 flex-col gap-1 overflow-y-auto rounded-md border border-border bg-bg-white p-2">
                    {venues.map((v) => {
                      const active = draft.venueIds.includes(v.id);
                      return (
                        <label
                          key={v.id}
                          className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm text-text-primary hover:bg-surface"
                        >
                          <input
                            type="checkbox"
                            checked={active}
                            onChange={() => toggleVenue(v.id)}
                            className="h-4 w-4"
                          />
                          <span>{v.name}</span>
                        </label>
                      );
                    })}
                  </div>
                </FilterSection>
              ) : null}

              <FilterSection title="Artist">
                <input
                  type="search"
                  value={draft.artistSearch ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      artistSearch: e.target.value || null,
                    })
                  }
                  placeholder="Search artist name…"
                  className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary"
                />
              </FilterSection>

              <FilterSection title="Price">
                <label className="mb-2 flex items-center gap-2 text-sm text-text-primary">
                  <input
                    type="checkbox"
                    checked={draft.freeOnly}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        freeOnly: e.target.checked,
                        priceMax: e.target.checked ? null : draft.priceMax,
                      })
                    }
                    className="h-4 w-4"
                  />
                  Free shows only
                </label>
                <label className="flex items-center gap-2 text-sm text-text-primary">
                  Max
                  <span className="text-text-secondary">$</span>
                  <input
                    type="number"
                    min="0"
                    step="5"
                    inputMode="decimal"
                    disabled={draft.freeOnly}
                    value={draft.priceMax ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      setDraft({
                        ...draft,
                        priceMax: v === "" ? null : Number.parseFloat(v),
                      });
                    }}
                    placeholder="No limit"
                    className="w-24 rounded-md border border-border bg-bg-white px-2 py-1.5 text-sm text-text-primary disabled:opacity-50"
                  />
                </label>
              </FilterSection>

              <FilterSection title="Availability">
                <label className="flex items-center gap-2 text-sm text-text-primary">
                  <input
                    type="checkbox"
                    checked={draft.availableOnly}
                    onChange={(e) =>
                      setDraft({ ...draft, availableOnly: e.target.checked })
                    }
                    className="h-4 w-4"
                  />
                  Hide sold-out & cancelled
                </label>
              </FilterSection>
            </div>

            <footer className="flex items-center justify-between gap-3 border-t border-border bg-bg-white px-5 py-3">
              <button
                type="button"
                onClick={onClearAll}
                disabled={isEmptyFilters(draft) && isEmptyFilters(initialFilters)}
                className="text-sm font-medium text-text-secondary transition hover:text-text-primary disabled:opacity-40"
              >
                Clear all
              </button>
              <button
                type="button"
                onClick={onApply}
                className="rounded-full bg-green-primary px-5 py-2 text-sm font-semibold text-text-inverse transition hover:bg-green-dark focus:outline-none focus:ring-2 focus:ring-green-soft"
              >
                Apply
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </>
  );
}

function FilterSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section className="mb-5 last:mb-0">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        {title}
      </h3>
      {children}
    </section>
  );
}
