/**
 * /login — Spotify OAuth entry point.
 *
 * Spotify OAuth is not yet wired end-to-end (credentials pending).
 * The page renders a disabled Connect button so the UI shell is
 * shippable; flipping the feature on is just adding the real
 * `onClick` handler once the backend `/api/v1/auth/spotify/start`
 * endpoint exists.
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth";

export default function LoginPage(): JSX.Element {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace("/for-you");
    }
  }, [isAuthenticated, isLoading, router]);

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
          disabled
          className="mt-6 w-full cursor-not-allowed rounded-lg bg-green-primary px-4 py-3 text-sm font-medium text-text-inverse opacity-60"
        >
          Connect Spotify (coming soon)
        </button>

        <p className="mt-4 text-xs text-text-secondary">
          Spotify OAuth is pending developer credentials. In the meantime, you
          can{" "}
          <Link href="/events" className="underline underline-offset-2">
            browse the full DMV calendar
          </Link>{" "}
          without an account.
        </p>
      </div>
    </main>
  );
}
