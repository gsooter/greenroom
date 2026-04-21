/**
 * Step 1 — Taste: genre tiles + artist search.
 *
 * Genres persist via ``PATCH /me`` (genre_preferences). Artists are
 * followed one at a time via ``POST /me/followed-artists/:id`` with a
 * local state mirror so multiple selections stay snappy without a
 * round-trip per click.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import {
  followArtist,
  searchArtists,
  unfollowArtist,
} from "@/lib/api/follows";
import { updateMe } from "@/lib/api/me";
import { listGenres } from "@/lib/api/onboarding";
import type { ArtistSummary, Genre, User } from "@/types";

interface Props {
  token: string;
  user: User;
  onDone: () => void;
  onSkip: () => void;
  onRefreshUser: () => Promise<void>;
}

export function TasteStep({
  token,
  user,
  onDone,
  onSkip,
  onRefreshUser,
}: Props): JSX.Element {
  const [genres, setGenres] = useState<Genre[]>([]);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(user.genre_preferences ?? []),
  );
  const [query, setQuery] = useState<string>("");
  const [results, setResults] = useState<ArtistSummary[]>([]);
  const [searching, setSearching] = useState<boolean>(false);
  const [followedIds, setFollowedIds] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listGenres()
      .then(setGenres)
      .catch(() => setGenres([]));
  }, []);

  // Debounce artist search so every keystroke doesn't hammer the API.
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const trimmed = query.trim();
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (trimmed.length < 2) {
      setResults([]);
      return;
    }
    searchTimer.current = setTimeout(() => {
      setSearching(true);
      void searchArtists(token, trimmed)
        .then((artists) => {
          setResults(artists);
          setFollowedIds((prev) => {
            const next = new Set(prev);
            for (const a of artists) {
              if (a.is_followed) next.add(a.id);
            }
            return next;
          });
        })
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 200);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [query, token]);

  const toggleGenre = useCallback((slug: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }, []);

  const toggleArtist = useCallback(
    async (artist: ArtistSummary) => {
      const isFollowed = followedIds.has(artist.id);
      setFollowedIds((prev) => {
        const next = new Set(prev);
        if (isFollowed) next.delete(artist.id);
        else next.add(artist.id);
        return next;
      });
      try {
        if (isFollowed) await unfollowArtist(token, artist.id);
        else await followArtist(token, artist.id);
      } catch (err) {
        // Revert on failure so the UI stays honest.
        setFollowedIds((prev) => {
          const next = new Set(prev);
          if (isFollowed) next.add(artist.id);
          else next.delete(artist.id);
          return next;
        });
        setError(
          err instanceof ApiRequestError
            ? err.message
            : "Could not update this artist. Try again.",
        );
      }
    },
    [followedIds, token],
  );

  const handleContinue = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const nextGenres = Array.from(selected);
      if (
        JSON.stringify(nextGenres.slice().sort()) !==
        JSON.stringify((user.genre_preferences ?? []).slice().sort())
      ) {
        await updateMe(token, { genre_preferences: nextGenres });
        await onRefreshUser();
      }
      onDone();
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not save your taste. Try again.",
      );
    } finally {
      setSaving(false);
    }
  }, [onDone, onRefreshUser, selected, token, user.genre_preferences]);

  const canContinue = useMemo(
    () => selected.size > 0 || followedIds.size > 0,
    [selected.size, followedIds.size],
  );

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-text-primary">
          What do you listen to?
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Pick a few genres you love and search for artists you follow. We use
          both to surface shows we think you&apos;ll like.
        </p>
      </header>

      <section>
        <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
          Genres
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {genres.map((g) => {
            const active = selected.has(g.slug);
            return (
              <button
                key={g.slug}
                type="button"
                onClick={() => toggleGenre(g.slug)}
                className={
                  active
                    ? "rounded-full bg-green-soft px-3 py-1.5 text-xs font-medium text-green-dark ring-1 ring-green-primary"
                    : "rounded-full bg-bg-surface px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-green-soft/60"
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
      </section>

      <section>
        <label className="block">
          <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
            Search artists
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Phoebe Bridgers, The Beths, Bon Iver…"
            className="mt-1 w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
            autoComplete="off"
          />
        </label>

        {searching ? (
          <p className="mt-3 text-xs text-text-secondary">Searching…</p>
        ) : null}

        {!searching && query.trim().length >= 2 && results.length === 0 ? (
          <p className="mt-3 text-xs text-text-secondary">
            No matches. We&apos;ll pull from Spotify in a future update.
          </p>
        ) : null}

        {results.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {results.map((artist) => {
              const isFollowed = followedIds.has(artist.id);
              return (
                <li
                  key={artist.id}
                  className="flex items-center justify-between rounded-md border border-border bg-bg-white px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-medium text-text-primary">
                      {artist.name}
                    </p>
                    {artist.genres.length > 0 ? (
                      <p className="text-[11px] text-text-secondary">
                        {artist.genres.slice(0, 3).join(" · ")}
                      </p>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => void toggleArtist(artist)}
                    className={
                      isFollowed
                        ? "rounded-md bg-green-primary px-3 py-1.5 text-xs font-medium text-text-inverse"
                        : "rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary hover:bg-green-primary hover:text-text-inverse"
                    }
                  >
                    {isFollowed ? "Following" : "Follow"}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

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
          disabled={saving || !canContinue}
          className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
        >
          {saving ? "Saving…" : "Continue"}
        </button>
      </div>
    </div>
  );
}
