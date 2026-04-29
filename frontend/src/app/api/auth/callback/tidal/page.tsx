/**
 * Tidal OAuth callback — `GET /api/auth/callback/tidal`.
 *
 * Tidal redirects here with `?code=...&state=...` after the user
 * approves the consent screen. Tidal is a *connect* flow (parallel to
 * Spotify): the caller is already authenticated and
 * `/auth/tidal/complete` just links the MusicServiceConnection and
 * returns the refreshed user. We forward the current session token as
 * the Bearer credential, refresh AuthContext so the UI picks up the
 * new connection state, and send the user back to `/settings`.
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { completeTidalOAuth } from "@/lib/api/auth";
import { markStepComplete } from "@/lib/api/onboarding";
import { useAuth } from "@/lib/auth";
import { consumeWelcomeReturnFlag } from "@/lib/welcome-redirect";

type Status = "pending" | "error";

export default function TidalCallbackPage(): JSX.Element {
  return (
    <Suspense fallback={<CallbackShell message="Finishing connection…" />}>
      <TidalCallbackInner />
    </Suspense>
  );
}

function CallbackShell({ message }: { message: string }): JSX.Element {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-text-primary">
          Connecting Tidal…
        </h1>
        <p className="mt-2 text-sm text-text-secondary">{message}</p>
      </div>
    </main>
  );
}

function TidalCallbackInner(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { token, isLoading, refreshUser } = useAuth();

  const [status, setStatus] = useState<Status>("pending");
  const [message, setMessage] = useState<string>("Finishing connection…");
  const hasRun = useRef(false);

  useEffect(() => {
    if (hasRun.current) return;
    if (isLoading) return;
    hasRun.current = true;

    const tidalError = params.get("error");
    const code = params.get("code");
    const state = params.get("state");

    if (tidalError) {
      setStatus("error");
      setMessage(
        tidalError === "access_denied"
          ? "Tidal connection was cancelled."
          : `Tidal returned an error: ${tidalError}`,
      );
      return;
    }
    if (!code || !state) {
      setStatus("error");
      setMessage("Missing code or state in callback URL.");
      return;
    }

    if (!token) {
      setStatus("error");
      setMessage("Your session expired — please sign in and retry.");
      return;
    }

    void (async () => {
      try {
        await completeTidalOAuth(token, code, state);
        await refreshUser();
        const fromWelcome = consumeWelcomeReturnFlag();
        if (fromWelcome) {
          try {
            await markStepComplete(token, "music_services");
          } catch {
            /* non-fatal */
          }
          router.replace("/welcome");
        } else {
          router.replace("/settings");
        }
      } catch (err) {
        setStatus("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "Could not complete Tidal connection.",
        );
      }
    })();
  }, [isLoading, params, token, refreshUser, router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
        {status === "pending" ? (
          <>
            <h1 className="text-lg font-semibold text-text-primary">
              Connecting Tidal…
            </h1>
            <p className="mt-2 text-sm text-text-secondary">{message}</p>
          </>
        ) : (
          <>
            <h1 className="text-lg font-semibold text-text-primary">
              Tidal connection didn&apos;t complete
            </h1>
            <p className="mt-2 text-sm text-blush-accent" role="alert">
              {message}
            </p>
            <Link
              href="/settings"
              className="mt-4 inline-block rounded-lg bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse hover:bg-green-dark"
            >
              Back to settings
            </Link>
          </>
        )}
      </div>
    </main>
  );
}
