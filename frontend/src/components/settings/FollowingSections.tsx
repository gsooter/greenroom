/**
 * Followed-artists and followed-venues lists for the settings page.
 *
 * Each list mirrors the relevant ``GET /me/followed-*`` endpoint and
 * exposes an inline unfollow action that hits ``DELETE /me/followed-*``.
 * Empty states deep-link to onboarding/discovery so a returning user
 * who unfollowed everything has somewhere to go.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  listFollowedArtists,
  listFollowedVenues,
  unfollowArtist,
  unfollowVenue,
} from "@/lib/api/follows";
import type { ArtistSummary, VenueSummary } from "@/types";

interface Props {
  token: string | null;
}

/**
 * Render the followed-artists and followed-venues sections side by side.
 *
 * Args:
 *     token: The current session token. ``null`` short-circuits both
 *         lists into a loading state until auth resolves.
 *
 * Returns:
 *     A ``<div>`` wrapping the two list sections.
 */
export function FollowingSections({ token }: Props): JSX.Element {
  return (
    <div className="space-y-10">
      <FollowedArtistsList token={token} />
      <FollowedVenuesList token={token} />
    </div>
  );
}

function FollowedArtistsList({ token }: Props): JSX.Element {
  const [artists, setArtists] = useState<ArtistSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!token) return;
    try {
      const res = await listFollowedArtists(token);
      setArtists(res.data);
    } catch {
      setArtists([]);
      setError("Could not load your followed artists.");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUnfollow = useCallback(
    async (id: string): Promise<void> => {
      if (!token) return;
      const previous = artists;
      setArtists((prev) => prev?.filter((a) => a.id !== id) ?? null);
      try {
        await unfollowArtist(token, id);
      } catch {
        setArtists(previous);
        setError("Could not unfollow that artist. Try again.");
      }
    },
    [token, artists],
  );

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Followed artists
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        Anyone here whose tour stops near you will surface in your For-You feed.
      </p>

      {artists === null ? (
        <p className="mt-4 text-xs text-text-secondary">Loading…</p>
      ) : artists.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-border bg-bg-white p-4">
          <p className="text-sm text-text-secondary">
            You aren&apos;t following any artists yet.{" "}
            <Link
              href="/welcome"
              className="text-text-primary underline underline-offset-2"
            >
              Pick a few from onboarding
            </Link>{" "}
            or follow them as you browse shows.
          </p>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-border rounded-lg border border-border bg-bg-white">
          {artists.map((artist) => (
            <li
              key={artist.id}
              className="flex items-center justify-between gap-4 p-4"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-text-primary">
                  {artist.name}
                </p>
                {artist.genres.length > 0 ? (
                  <p className="mt-0.5 truncate text-[11px] text-text-secondary">
                    {artist.genres.slice(0, 3).join(" · ")}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void handleUnfollow(artist.id)}
                className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary transition hover:border-blush-accent hover:text-blush-accent"
              >
                Unfollow
              </button>
            </li>
          ))}
        </ul>
      )}

      {error ? (
        <p className="mt-3 text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function FollowedVenuesList({ token }: Props): JSX.Element {
  const [venues, setVenues] = useState<VenueSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!token) return;
    try {
      const res = await listFollowedVenues(token);
      setVenues(res.data);
    } catch {
      setVenues([]);
      setError("Could not load your followed venues.");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUnfollow = useCallback(
    async (id: string): Promise<void> => {
      if (!token) return;
      const previous = venues;
      setVenues((prev) => prev?.filter((v) => v.id !== id) ?? null);
      try {
        await unfollowVenue(token, id);
      } catch {
        setVenues(previous);
        setError("Could not unfollow that venue. Try again.");
      }
    },
    [token, venues],
  );

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Followed venues
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        Shows at these rooms get a soft boost in your feed and digest.
      </p>

      {venues === null ? (
        <p className="mt-4 text-xs text-text-secondary">Loading…</p>
      ) : venues.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-border bg-bg-white p-4">
          <p className="text-sm text-text-secondary">
            You aren&apos;t following any venues yet.{" "}
            <Link
              href="/venues"
              className="text-text-primary underline underline-offset-2"
            >
              Browse the venue directory
            </Link>{" "}
            to find your regulars.
          </p>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-border rounded-lg border border-border bg-bg-white">
          {venues.map((venue) => (
            <li
              key={venue.id}
              className="flex items-center justify-between gap-4 p-4"
            >
              <div className="min-w-0">
                <Link
                  href={`/venues/${venue.slug}`}
                  className="truncate text-sm font-medium text-text-primary underline-offset-2 hover:underline"
                >
                  {venue.name}
                </Link>
                {venue.city ? (
                  <p className="mt-0.5 truncate text-[11px] text-text-secondary">
                    {venue.city.name}, {venue.city.state}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void handleUnfollow(venue.id)}
                className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary transition hover:border-blush-accent hover:text-blush-accent"
              >
                Unfollow
              </button>
            </li>
          ))}
        </ul>
      )}

      {error ? (
        <p className="mt-3 text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
