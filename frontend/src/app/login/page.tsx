/**
 * /login — Spotify OAuth entry point.
 *
 * Calls the backend `/auth/spotify/start` to obtain the consent URL
 * (which already carries a signed `state`) and navigates the browser
 * there. Spotify redirects back to `/api/auth/callback/spotify`.
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { startSpotifyOAuth } from "@/lib/api/auth";
import { useAuth } from "@/lib/auth";

export default function LoginPage(): JSX.Element {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace("/for-you");
    }
  }, [isAuthenticated, isLoading, router]);

  async function handleConnect(): Promise<void> {
    setIsConnecting(true);
    setError(null);
    try {
      const { authorize_url } = await startSpotifyOAuth();
      window.location.href = authorize_url;
    } catch (err) {
      setIsConnecting(false);
      setError(
        err instanceof Error ? err.message : "Could not start Spotify sign-in.",
      );
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
        <h1 className="text-2xl font-semibold text-text-primary">
          Sign in to Greenroom
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          Connect Spotify to unlock personalized concert picks, save shows, and
          get a weekly digest of what&apos;s coming up in the DMV.
        </p>

        <button
          type="button"
          onClick={handleConnect}
          disabled={isConnecting}
          className="mt-6 w-full rounded-lg bg-green-primary px-4 py-3 text-sm font-medium text-text-inverse transition hover:bg-green-dark disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isConnecting ? "Redirecting to Spotify…" : "Connect Spotify"}
        </button>

        {error ? (
          <p className="mt-3 text-xs text-blush-accent" role="alert">
            {error}
          </p>
        ) : null}

        <p className="mt-4 text-xs text-text-secondary">
          Prefer to look around first?{" "}
          <Link href="/events" className="underline underline-offset-2">
            Browse the full DMV calendar
          </Link>{" "}
          without an account.
        </p>
      </div>
    </main>
  );
}
