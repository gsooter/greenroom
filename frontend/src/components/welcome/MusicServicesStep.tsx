/**
 * Step 3 — Music Services: reuse the existing connect flows.
 *
 * Spotify and Tidal are full-page OAuth redirects. To keep the flow
 * anchored, we stash a sessionStorage marker before redirect so the
 * auth callback (Task #72) can bring the user back to /welcome.
 * Apple Music uses the in-page MusicKit consent sheet and finishes
 * without a redirect, so we call ``onRefreshConnections`` inline.
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  connectAppleMusic,
  getAppleMusicDeveloperToken,
  startSpotifyOAuth,
  startTidalOAuth,
} from "@/lib/api/auth";
import { getMyMusicConnections } from "@/lib/api/me";
import { authorizeAppleMusic } from "@/lib/musickit";
import type { MusicConnectionState, MusicProvider } from "@/types";

import { StepIntro } from "./StepIntro";

const RETURN_KEY = "greenroom.welcome_return";

interface Props {
  token: string;
  onDone: () => void;
  onSkip: () => void;
}

const PROVIDER_LABEL: Record<MusicProvider, string> = {
  spotify: "Spotify",
  tidal: "Tidal",
  apple_music: "Apple Music",
};

export function MusicServicesStep({
  token,
  onDone,
  onSkip,
}: Props): JSX.Element {
  const [connections, setConnections] = useState<MusicConnectionState[]>([]);
  const [busy, setBusy] = useState<MusicProvider | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reloadConnections = useCallback(async () => {
    try {
      const res = await getMyMusicConnections(token);
      setConnections(res.connections);
    } catch {
      /* best-effort */
    }
  }, [token]);

  useEffect(() => {
    void reloadConnections();
  }, [reloadConnections]);

  const anyConnected = useMemo(
    () => connections.some((c) => c.connected),
    [connections],
  );

  function markReturnToWelcome(): void {
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(RETURN_KEY, "music_services");
    }
  }

  const handleSpotify = useCallback(async () => {
    setBusy("spotify");
    setError(null);
    try {
      const { authorize_url } = await startSpotifyOAuth(token);
      markReturnToWelcome();
      window.location.href = authorize_url;
    } catch (err) {
      setBusy(null);
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Spotify connection.",
      );
    }
  }, [token]);

  const handleTidal = useCallback(async () => {
    setBusy("tidal");
    setError(null);
    try {
      const { authorize_url } = await startTidalOAuth(token);
      markReturnToWelcome();
      window.location.href = authorize_url;
    } catch (err) {
      setBusy(null);
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Tidal connection.",
      );
    }
  }, [token]);

  const handleAppleMusic = useCallback(async () => {
    setBusy("apple_music");
    setError(null);
    try {
      const { developer_token } = await getAppleMusicDeveloperToken(token);
      const mut = await authorizeAppleMusic({
        developerToken: developer_token,
        appName: "Greenroom",
        appBuild: "1.0.0",
      });
      await connectAppleMusic(token, mut);
      await reloadConnections();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not connect Apple Music.",
      );
    } finally {
      setBusy(null);
    }
  }, [reloadConnections, token]);

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-text-primary">
          Supercharge with your library
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Connect a music service and we&apos;ll tag shows by artists you
          actually listen to. You can always do this later from Settings.
        </p>
      </header>

      <StepIntro>
        This is the magic-dust step. Connect Spotify, Tidal, or Apple Music and
        every DC show featuring an artist you listen to gets a &ldquo;You listen
        to X&rdquo; chip — so nothing slips past you.
      </StepIntro>

      <div className="space-y-3">
        <ProviderRow
          provider="spotify"
          state={connections.find((c) => c.provider === "spotify")}
          busy={busy === "spotify"}
          onConnect={() => void handleSpotify()}
        />
        <ProviderRow
          provider="tidal"
          state={connections.find((c) => c.provider === "tidal")}
          busy={busy === "tidal"}
          onConnect={() => void handleTidal()}
        />
        <ProviderRow
          provider="apple_music"
          state={connections.find((c) => c.provider === "apple_music")}
          busy={busy === "apple_music"}
          onConnect={() => void handleAppleMusic()}
        />
      </div>

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
          onClick={onDone}
          className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse"
        >
          {anyConnected ? "Continue" : "Continue without"}
        </button>
      </div>
    </div>
  );
}

function ProviderRow({
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
  return (
    <div className="flex items-center justify-between rounded-lg border border-border bg-bg-white p-4">
      <div>
        <p className="text-sm font-medium text-text-primary">{label}</p>
        <p className="mt-1 text-xs text-text-secondary">
          {connected
            ? state?.artist_count
              ? `Connected — ${state.artist_count} artists synced.`
              : "Connected."
            : "Not connected yet."}
        </p>
      </div>
      <button
        type="button"
        onClick={onConnect}
        disabled={busy}
        className="rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary transition hover:bg-green-primary hover:text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
      >
        {busy ? "Working…" : connected ? "Reconnect" : `Connect ${label}`}
      </button>
    </div>
  );
}
