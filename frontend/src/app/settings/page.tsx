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

import {
  connectAppleMusic,
  getAppleMusicDeveloperToken,
  startSpotifyOAuth,
  startTidalOAuth,
} from "@/lib/api/auth";
import {
  completePasskeyRegistration,
  startPasskeyRegistration,
} from "@/lib/api/auth-identity";
import { ApiRequestError } from "@/lib/api/client";
import { listCities } from "@/lib/api/cities";
import { deleteMe, getMyMusicConnections, updateMe } from "@/lib/api/me";
import { useRequireAuth } from "@/lib/auth";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "@/lib/config";
import { authorizeAppleMusic } from "@/lib/musickit";
import {
  TIMEZONE_OPTIONS,
  useDistanceUnit,
  useTimezonePreference,
} from "@/lib/preferences";
import {
  decodeRegistrationOptions,
  encodeRegistrationCredential,
  isWebAuthnSupported,
} from "@/lib/webauthn";
import type {
  City,
  DigestFrequency,
  MusicConnectionState,
  MusicProvider,
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

  const [connections, setConnections] = useState<MusicConnectionState[]>([]);

  const loadConnections = useCallback(async (): Promise<void> => {
    if (!token) return;
    try {
      const res = await getMyMusicConnections(token);
      setConnections(res.connections);
    } catch {
      /* best-effort read */
    }
  }, [token]);

  useEffect(() => {
    void listCities().then((list) => setCities(list));
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

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
        token={token}
        connections={connections}
        onConnectionChange={() => void loadConnections()}
      />

      <hr className="my-10 border-border" />

      <SecuritySection token={token} />

      <hr className="my-10 border-border" />

      <section>
        <h2 className="text-base font-semibold text-text-primary">
          Help &amp; support
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Stuck on something, spotted a bug, or want to suggest a venue?{" "}
          <a
            href={SUPPORT_MAILTO}
            className="text-text-primary underline underline-offset-2"
          >
            {SUPPORT_EMAIL}
          </a>{" "}
          reaches a real human.
        </p>
      </section>

      <hr className="my-10 border-border" />

      <DisplayPreferencesSection />

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

function DisplayPreferencesSection(): JSX.Element {
  const [unit, setUnit] = useDistanceUnit();
  const [timezone, setTimezone] = useTimezonePreference();

  const knownZone = TIMEZONE_OPTIONS.some((opt) => opt.value === timezone);
  const zoneValue = knownZone ? timezone : "__custom__";

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Display preferences
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        How values are shown on this device. Stored locally — not synced across
        browsers.
      </p>

      <div className="mt-4 divide-y divide-border rounded-lg border border-border bg-bg-white">
        <PreferenceRow
          label="Distance"
          description="Used for venue distance pills and the near-me sort."
        >
          <div
            role="radiogroup"
            aria-label="Distance units"
            className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-surface p-1"
          >
            {(
              [
                { value: "mi" as const, label: "mi" },
                { value: "km" as const, label: "km" },
              ]
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={unit === opt.value}
                onClick={() => setUnit(opt.value)}
                className={
                  "rounded-full px-3 py-1 text-xs font-medium uppercase tracking-wide transition " +
                  (unit === opt.value
                    ? "bg-green-primary text-text-inverse"
                    : "text-text-secondary hover:text-text-primary")
                }
              >
                {opt.label}
              </button>
            ))}
          </div>
        </PreferenceRow>

        <PreferenceRow
          label="Event times"
          description="Applied to every show date and door time on the site."
        >
          <select
            aria-label="Event time zone"
            value={zoneValue}
            onChange={(e) => {
              if (e.target.value !== "__custom__") setTimezone(e.target.value);
            }}
            className="rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          >
            {TIMEZONE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
            {!knownZone ? (
              <option value="__custom__">Custom: {timezone}</option>
            ) : null}
          </select>
        </PreferenceRow>
      </div>
    </section>
  );
}

function PreferenceRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary">{label}</p>
        <p className="mt-0.5 text-xs text-text-secondary">{description}</p>
      </div>
      <div className="shrink-0 self-start sm:self-auto">{children}</div>
    </div>
  );
}

const PROVIDER_LABEL: Record<MusicProvider, string> = {
  spotify: "Spotify",
  tidal: "Tidal",
  apple_music: "Apple Music",
};

const PROVIDER_PITCH: Record<MusicProvider, string> = {
  spotify: "Link Spotify to power personalized picks.",
  tidal: "Connect Tidal to add your favorite artists to the recommender.",
  apple_music: "Connect Apple Music to add your library to the recommender.",
};

// Each service exposes a different "which artists do we pull" signal
// because its API surface differs. Shown as a small caption so users
// know what's feeding the recommender rather than assuming parity.
const PROVIDER_SIGNAL_NOTE: Record<MusicProvider, string> = {
  spotify: "Uses your top and recently-played artists.",
  apple_music: "Uses artists saved in your library.",
  tidal: "Uses artists in your favorites collection.",
};

function connectionByProvider(
  connections: MusicConnectionState[],
  provider: MusicProvider,
): MusicConnectionState | undefined {
  return connections.find((c) => c.provider === provider);
}

function providerStatusMessage(
  provider: MusicProvider,
  state: MusicConnectionState | undefined,
): string {
  if (!state || !state.connected) return PROVIDER_PITCH[provider];
  if (state.artist_count === 0) {
    return state.synced_at
      ? `Connected, but your library looked empty on ${formatSyncedAt(state.synced_at)}.`
      : "Connected, but we couldn't pull your library. We'll retry on your next visit.";
  }
  const when = state.synced_at ? formatSyncedAt(state.synced_at) : "recently";
  return `Connected. ${state.artist_count} artists synced ${when}.`;
}

function ConnectedServicesSection({
  token,
  connections,
  onConnectionChange,
}: {
  token: string | null;
  connections: MusicConnectionState[];
  onConnectionChange: () => void;
}): JSX.Element {
  const [spotifyConnecting, setSpotifyConnecting] = useState<boolean>(false);
  const [tidalConnecting, setTidalConnecting] = useState<boolean>(false);
  const [appleConnecting, setAppleConnecting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const spotifyState = connectionByProvider(connections, "spotify");
  const tidalState = connectionByProvider(connections, "tidal");
  const appleState = connectionByProvider(connections, "apple_music");

  async function handleSpotifyConnect(): Promise<void> {
    if (!token) {
      setError("You need to be signed in to connect Spotify.");
      return;
    }
    setSpotifyConnecting(true);
    setError(null);
    try {
      const { authorize_url } = await startSpotifyOAuth(token);
      window.location.href = authorize_url;
    } catch (err) {
      setSpotifyConnecting(false);
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Spotify connection.",
      );
    }
  }

  async function handleTidalConnect(): Promise<void> {
    if (!token) {
      setError("You need to be signed in to connect Tidal.");
      return;
    }
    setTidalConnecting(true);
    setError(null);
    try {
      const { authorize_url } = await startTidalOAuth(token);
      window.location.href = authorize_url;
    } catch (err) {
      setTidalConnecting(false);
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Tidal connection.",
      );
    }
  }

  async function handleAppleMusicConnect(): Promise<void> {
    if (!token) {
      setError("You need to be signed in to connect Apple Music.");
      return;
    }
    setAppleConnecting(true);
    setError(null);
    try {
      const { developer_token } = await getAppleMusicDeveloperToken(token);
      const mut = await authorizeAppleMusic({
        developerToken: developer_token,
        appName: "Greenroom",
        appBuild: "1.0.0",
      });
      await connectAppleMusic(token, mut);
      onConnectionChange();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not connect Apple Music.",
      );
    } finally {
      setAppleConnecting(false);
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

      <ServiceCard
        provider="spotify"
        state={spotifyState}
        busy={spotifyConnecting}
        onConnect={() => void handleSpotifyConnect()}
      />

      <ServiceCard
        provider="tidal"
        state={tidalState}
        busy={tidalConnecting}
        onConnect={() => void handleTidalConnect()}
      />

      <ServiceCard
        provider="apple_music"
        state={appleState}
        busy={appleConnecting}
        onConnect={() => void handleAppleMusicConnect()}
      />

      {error ? (
        <p className="mt-3 text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function ServiceCard({
  provider,
  state,
  busy,
  onConnect,
}: {
  provider: MusicProvider;
  state: MusicConnectionState | undefined;
  busy: boolean;
  onConnect: () => void;
}): JSX.Element {
  const label = PROVIDER_LABEL[provider];
  const connected = Boolean(state?.connected);
  const busyLabel =
    provider === "apple_music" ? "Authorizing…" : "Redirecting…";
  const artists = state?.artists ?? [];
  return (
    <div className="mt-3 rounded-lg border border-border bg-bg-white p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-text-primary">{label}</p>
          <p className="mt-1 text-xs text-text-secondary">
            {providerStatusMessage(provider, state)}
          </p>
          <p className="mt-1 text-[11px] italic text-text-secondary/80">
            {PROVIDER_SIGNAL_NOTE[provider]}
          </p>
        </div>
        <button
          type="button"
          onClick={onConnect}
          disabled={busy}
          className="rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary transition hover:bg-green-primary hover:text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? busyLabel : connected ? "Reconnect" : `Connect ${label}`}
        </button>
      </div>
      {artists.length > 0 ? (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Your rotation
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {artists.map((artist) => (
              <li
                key={`${provider}-${artist.id ?? artist.name}`}
                className="rounded-full bg-blush-soft px-3 py-1 text-xs font-medium text-blush-accent"
              >
                {artist.name}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
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
      <h2 className="text-base font-semibold text-text-primary">Security</h2>
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
              {status === "registering"
                ? "Waiting for passkey…"
                : "Add a passkey"}
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
