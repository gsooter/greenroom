/**
 * /settings — profile + notification preferences.
 *
 * CSR only. Editable fields mirror the PATCH /me allowlist:
 * display name, preferred city, digest frequency, genre preferences.
 * Deactivation is wired to DELETE /me.
 */

"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import { listCities } from "@/lib/api/cities";
import { deleteMe, updateMe } from "@/lib/api/me";
import { getMyTopArtists } from "@/lib/api/recommendations";
import { useRequireAuth } from "@/lib/auth";
import type {
  City,
  DigestFrequency,
  SpotifyTopArtist,
  UserPatch,
} from "@/types";

const DIGEST_OPTIONS: DigestFrequency[] = ["daily", "weekly", "never"];

export default function SettingsPage(): JSX.Element {
  const router = useRouter();
  const { user, token, isLoading, isAuthenticated, refresh, logout } =
    useRequireAuth();

  const [cities, setCities] = useState<City[]>([]);
  const [displayName, setDisplayName] = useState<string>("");
  const [cityId, setCityId] = useState<string>("");
  const [digest, setDigest] = useState<DigestFrequency>("weekly");
  const [genres, setGenres] = useState<string>("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);

  const [topArtists, setTopArtists] = useState<SpotifyTopArtist[]>([]);
  const [topArtistsSyncedAt, setTopArtistsSyncedAt] = useState<string | null>(
    null,
  );

  useEffect(() => {
    void listCities().then((list) => setCities(list));
  }, []);

  useEffect(() => {
    if (!token) return;
    void getMyTopArtists(token)
      .then((res) => {
        setTopArtists(res.artists);
        setTopArtistsSyncedAt(res.synced_at);
      })
      .catch(() => {
        /* top-artists is a best-effort read; do nothing on failure */
      });
  }, [token]);

  useEffect(() => {
    if (!user) return;
    setDisplayName(user.display_name ?? "");
    setCityId(user.city_id ?? "");
    setDigest(user.digest_frequency);
    setGenres((user.genre_preferences ?? []).join(", "));
  }, [user]);

  const patch = useMemo<UserPatch>(() => {
    if (!user) return {};
    const next: UserPatch = {};
    const trimmedName = displayName.trim();
    if ((user.display_name ?? "") !== trimmedName) {
      next.display_name = trimmedName === "" ? null : trimmedName;
    }
    const normalizedCity = cityId === "" ? null : cityId;
    if ((user.city_id ?? null) !== normalizedCity) {
      next.city_id = normalizedCity;
    }
    if (user.digest_frequency !== digest) {
      next.digest_frequency = digest;
    }
    const genreList = genres
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    const currentGenres = user.genre_preferences ?? [];
    if (JSON.stringify(currentGenres) !== JSON.stringify(genreList)) {
      next.genre_preferences = genreList;
    }
    return next;
  }, [user, displayName, cityId, digest, genres]);

  const hasChanges = Object.keys(patch).length > 0;

  const handleSave = useCallback(
    async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
      event.preventDefault();
      if (!token || !hasChanges) return;
      setStatus("saving");
      try {
        await updateMe(token, patch);
        await refresh();
        setStatus("saved");
      } catch (err) {
        setStatus("error");
        setError(
          err instanceof ApiRequestError
            ? err.message
            : "Could not save changes.",
        );
      }
    },
    [token, hasChanges, patch, refresh],
  );

  const handleDelete = useCallback(async (): Promise<void> => {
    if (!token) return;
    const confirmed = window.confirm(
      "Deactivate your account? You can contact support to reactivate later.",
    );
    if (!confirmed) return;
    try {
      await deleteMe(token);
      logout();
      router.replace("/");
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not deactivate account.",
      );
    }
  }, [token, logout, router]);

  if (isLoading || !isAuthenticated || !user) {
    return <PageShell>Loading…</PageShell>;
  }

  return (
    <PageShell>
      <h1 className="text-2xl font-semibold text-text-primary">Settings</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Signed in as {user.email}
      </p>

      <form className="mt-6 space-y-6" onSubmit={(e) => void handleSave(e)}>
        <Field label="Display name">
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          />
        </Field>

        <Field label="Preferred city">
          <select
            value={cityId}
            onChange={(e) => setCityId(e.target.value)}
            className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          >
            <option value="">No preference</option>
            {cities.map((city) => (
              <option key={city.id} value={city.id}>
                {city.name}, {city.state}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Email digest">
          <select
            value={digest}
            onChange={(e) => setDigest(e.target.value as DigestFrequency)}
            className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          >
            {DIGEST_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Favorite genres (comma-separated)">
          <input
            type="text"
            value={genres}
            onChange={(e) => setGenres(e.target.value)}
            placeholder="indie, electronic, post-punk"
            className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          />
        </Field>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!hasChanges || status === "saving"}
            className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:opacity-50"
          >
            {status === "saving" ? "Saving…" : "Save changes"}
          </button>
          {status === "saved" && (
            <span className="text-xs text-text-secondary">Saved.</span>
          )}
          {status === "error" && error && (
            <span className="text-xs text-blush-accent">{error}</span>
          )}
        </div>
      </form>

      <hr className="my-10 border-border" />

      <section>
        <h2 className="text-base font-semibold text-text-primary">
          Your Spotify rotation
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          These are the artists currently driving your recommendations.
          {topArtistsSyncedAt
            ? ` Last synced ${formatSyncedAt(topArtistsSyncedAt)}.`
            : ""}
        </p>
        {topArtists.length === 0 ? (
          <p className="mt-4 text-sm text-text-secondary">
            We haven&apos;t synced your Spotify listening history yet. Sign in with
            Spotify again or check back after our nightly sync.
          </p>
        ) : (
          <ul className="mt-4 flex flex-wrap gap-2">
            {topArtists.slice(0, 24).map((artist) => (
              <li
                key={`${artist.id ?? artist.name}`}
                className="rounded-full bg-blush-soft px-3 py-1 text-xs font-medium text-blush-accent"
              >
                {artist.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <hr className="my-10 border-border" />

      <section>
        <h2 className="text-base font-semibold text-text-primary">
          Danger zone
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Deactivate your account. Saved shows stay linked for analytics but
          future requests with your current session will be rejected.
        </p>
        <button
          type="button"
          onClick={() => void handleDelete()}
          className="mt-4 rounded-md border border-blush-accent px-4 py-2 text-sm font-medium text-blush-accent hover:bg-blush-soft"
        >
          Deactivate account
        </button>
      </section>
    </PageShell>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <label className="block">
      <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
        {label}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function PageShell({ children }: { children: React.ReactNode }): JSX.Element {
  return <main className="mx-auto max-w-2xl px-6 py-12">{children}</main>;
}

function formatSyncedAt(iso: string): string {
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "recently";
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return "recently";
  }
}
