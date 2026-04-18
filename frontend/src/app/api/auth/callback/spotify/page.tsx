/**
 * Spotify OAuth callback — `GET /api/auth/callback/spotify`.
 *
 * Spotify redirects here with `?code=...&state=...` after the user
 * approves the consent screen. We POST both values to the backend's
 * `/auth/spotify/complete`, store the returned JWT via AuthContext,
 * and send the user to `/for-you`.
 *
 * This route lives under `app/api/.../page.tsx` (not `route.ts`) on
 * purpose: we need `AuthContext.login(token)` to write localStorage,
 * which is a browser-only capability. A server route handler could
 * not call into the client-side auth store.
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { completeSpotifyOAuth } from "@/lib/api/auth";
import { useAuth } from "@/lib/auth";

type Status = "pending" | "error";

export default function SpotifyCallbackPage(): JSX.Element {
  return (
    <Suspense fallback={<CallbackShell message="Finishing sign-in…" />}>
      <SpotifyCallbackInner />
    </Suspense>
  );
}

function CallbackShell({ message }: { message: string }): JSX.Element {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-text-primary">
          Signing you in…
        </h1>
        <p className="mt-2 text-sm text-text-secondary">{message}</p>
      </div>
    </main>
  );
}

function SpotifyCallbackInner(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();

  const [status, setStatus] = useState<Status>("pending");
  const [message, setMessage] = useState<string>("Finishing sign-in…");
  const hasRun = useRef(false);

  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;

    const spotifyError = params.get("error");
    const code = params.get("code");
    const state = params.get("state");

    if (spotifyError) {
      setStatus("error");
      setMessage(
        spotifyError === "access_denied"
          ? "Spotify sign-in was cancelled."
          : `Spotify returned an error: ${spotifyError}`,
      );
      return;
    }
    if (!code || !state) {
      setStatus("error");
      setMessage("Missing code or state in callback URL.");
      return;
    }

    void (async () => {
      try {
        const { token } = await completeSpotifyOAuth(code, state);
        await login(token);
        router.replace("/for-you");
      } catch (err) {
        setStatus("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "Could not complete Spotify sign-in.",
        );
      }
    })();
  }, [params, login, router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
        {status === "pending" ? (
          <>
            <h1 className="text-lg font-semibold text-text-primary">
              Signing you in…
            </h1>
            <p className="mt-2 text-sm text-text-secondary">{message}</p>
          </>
        ) : (
          <>
            <h1 className="text-lg font-semibold text-text-primary">
              Sign-in didn&apos;t complete
            </h1>
            <p className="mt-2 text-sm text-blush-accent" role="alert">
              {message}
            </p>
            <Link
              href="/login"
              className="mt-4 inline-block rounded-lg bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse hover:bg-green-dark"
            >
              Try again
            </Link>
          </>
        )}
      </div>
    </main>
  );
}
