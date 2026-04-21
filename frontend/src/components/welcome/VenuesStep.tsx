/**
 * Step 2 — Venues: grid batch follow.
 *
 * Lists active DMV venues as tappable cards. A single
 * ``POST /me/followed-venues`` write lands every selection at once on
 * continue, so users can toggle freely without hammering the API.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import { followVenuesBulk } from "@/lib/api/follows";
import { listVenues } from "@/lib/api/venues";
import type { VenueSummary } from "@/types";

interface Props {
  token: string;
  onDone: () => void;
  onSkip: () => void;
}

export function VenuesStep({ token, onDone, onSkip }: Props): JSX.Element {
  const [venues, setVenues] = useState<VenueSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listVenues({ region: "DMV", activeOnly: true, perPage: 100 })
      .then((page) => {
        if (!cancelled) setVenues(page.data);
      })
      .catch(() => {
        if (!cancelled) setVenues([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback((venueId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(venueId)) next.delete(venueId);
      else next.add(venueId);
      return next;
    });
  }, []);

  const handleContinue = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      if (selected.size > 0) {
        await followVenuesBulk(token, Array.from(selected));
      }
      onDone();
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not save your venues. Try again.",
      );
    } finally {
      setSaving(false);
    }
  }, [onDone, selected, token]);

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-text-primary">
          Favorite venues?
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Pick every DC venue you already love going to. We&apos;ll nudge you
          when they announce a show that fits your taste.
        </p>
      </header>

      {loading ? (
        <p className="text-sm text-text-secondary">Loading DMV venues…</p>
      ) : venues.length === 0 ? (
        <p className="text-sm text-text-secondary">
          No venues available right now. Skip for now and try again later.
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {venues.map((venue) => {
            const active = selected.has(venue.id);
            return (
              <li key={venue.id}>
                <button
                  type="button"
                  onClick={() => toggle(venue.id)}
                  aria-pressed={active}
                  className={
                    active
                      ? "w-full rounded-lg border border-green-primary bg-green-soft p-3 text-left ring-2 ring-green-primary"
                      : "w-full rounded-lg border border-border bg-bg-white p-3 text-left hover:border-green-primary"
                  }
                >
                  <p className="text-sm font-medium text-text-primary">
                    {venue.name}
                  </p>
                  {venue.address ? (
                    <p className="mt-1 line-clamp-1 text-[11px] text-text-secondary">
                      {venue.address}
                    </p>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {error ? (
        <p className="text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}

      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onSkip}
          className="text-xs font-medium text-text-secondary underline underline-offset-2"
        >
          Skip for now
        </button>
        <button
          type="button"
          onClick={() => void handleContinue()}
          disabled={saving}
          className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
        >
          {saving
            ? "Saving…"
            : selected.size > 0
              ? `Follow ${selected.size} & continue`
              : "Continue"}
        </button>
      </div>
    </div>
  );
}
