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

import { startSpotifyOAuth } from "@/lib/api/auth";
import {
  completePasskeyRegistration,
  startPasskeyRegistration,
} from "@/lib/api/auth-identity";
import { ApiRequestError } from "@/lib/api/client";
import { listCities } from "@/lib/api/cities";
import { deleteMe, updateMe } from "@/lib/api/me";
import { getMyTopArtists } from "@/lib/api/recommendations";
import { useRequireAuth } from "@/lib/auth";
import {
  decodeRegistrationOptions,
  encodeRegistrationCredential,
  isWebAuthnSupported,
} from "@/lib/webauthn";
import type {
  City,
  DigestFrequency,
  SpotifyTopArtist,
  UserPatch,
} from "@/types";

const DIGEST_OPTIONS: DigestFrequency[] = ["daily", "weekly", "never"];

export default function SettingsPage(): JSX.Element {
  const router = useRouter();
  const { user, token, isLoading, isAuthenticated, refreshUser, logout } =
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
        await refreshUser();
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
    [token, hasChanges, patch, refreshUser],
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

      <ConnectedServicesSection
        topArtists={topArtists}
        topArtistsSyncedAt={topArtistsSyncedAt}
      />

      <hr className="my-10 border-border" />

      <SecuritySection token={token} />

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

function ConnectedServicesSection({
  topArtists,
  topArtistsSyncedAt,
}: {
  topArtists: SpotifyTopArtist[];
  topArtistsSyncedAt: string | null;
}): JSX.Element {
  const [connecting, setConnecting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const isConnected = topArtists.length > 0 || Boolean(topArtistsSyncedAt);

  async function handleConnect(): Promise<void> {
    setConnecting(true);
    setError(null);
    try {
      const { authorize_url } = await startSpotifyOAuth();
      window.location.href = authorize_url;
    } catch (err) {
      setConnecting(false);
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Spotify connection.",
      );
    }
  }

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Connected services
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        Link music services to improve your For-You picks. These are optional —
        Greenroom works without them.
      </p>

      <div className="mt-4 rounded-lg border border-border bg-bg-white p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-text-primary">Spotify</p>
            <p className="mt-1 text-xs text-text-secondary">
              {isConnected
                ? `Connected. Last synced ${
                    topArtistsSyncedAt
                      ? formatSyncedAt(topArtistsSyncedAt)
                      : "recently"
                  }.`
                : "Not connected. Link Spotify to power personalized picks."}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void handleConnect()}
            disabled={connecting}
            className="rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary transition hover:bg-green-primary hover:text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
          >
            {connecting
              ? "Redirecting…"
              : isConnected
                ? "Reconnect"
                : "Connect Spotify"}
          </button>
        </div>
        {error ? (
          <p className="mt-3 text-xs text-blush-accent" role="alert">
            {error}
          </p>
        ) : null}

        {topArtists.length > 0 ? (
          <div className="mt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Your rotation
            </p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {topArtists.slice(0, 24).map((artist) => (
                <li
                  key={`${artist.id ?? artist.name}`}
                  className="rounded-full bg-blush-soft px-3 py-1 text-xs font-medium text-blush-accent"
                >
                  {artist.name}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function SecuritySection({ token }: { token: string | null }): JSX.Element {
  const [supported, setSupported] = useState<boolean>(true);
  const [status, setStatus] = useState<
    "idle" | "naming" | "registering" | "done" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState<string>("");

  useEffect(() => {
    setSupported(isWebAuthnSupported());
  }, []);

  async function handleAdd(): Promise<void> {
    if (!token) return;
    setStatus("registering");
    setError(null);
    try {
      const { options, state } = await startPasskeyRegistration(token);
      const credential = (await navigator.credentials.create({
        publicKey: decodeRegistrationOptions(options),
      })) as PublicKeyCredential | null;
      if (!credential) {
        throw new Error("Passkey creation was cancelled.");
      }
      await completePasskeyRegistration(
        token,
        encodeRegistrationCredential(credential),
        state,
        name.trim() || undefined,
      );
      setStatus("done");
      setName("");
    } catch (err) {
      setStatus("error");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Passkey creation was cancelled.");
      } else {
        setError(
          err instanceof Error ? err.message : "Could not register a passkey.",
        );
      }
    }
  }

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Security
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        Add a passkey to sign in without email links. Passkeys are stored on
        your device (Touch ID, Face ID, Windows Hello, or a security key).
      </p>

      <div className="mt-4 rounded-lg border border-border bg-bg-white p-4">
        {!supported ? (
          <p className="text-xs text-text-secondary">
            This browser does not support passkeys yet. Try Safari 16+, Chrome
            108+, or Firefox 122+.
          </p>
        ) : (
          <div className="space-y-3">
            <Field label="Device label (optional)">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="MacBook Pro"
                className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
              />
            </Field>
            <button
              type="button"
              onClick={() => void handleAdd()}
              disabled={status === "registering" || !token}
              className="rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary transition hover:bg-green-primary hover:text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
            >
              {status === "registering" ? "Waiting for passkey…" : "Add a passkey"}
            </button>
            {status === "done" ? (
              <p className="text-xs text-text-secondary" role="status">
                Passkey registered. You can sign in with it next time.
              </p>
            ) : null}
            {status === "error" && error ? (
              <p className="text-xs text-blush-accent" role="alert">
                {error}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
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
