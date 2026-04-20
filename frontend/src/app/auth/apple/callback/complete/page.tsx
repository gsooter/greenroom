/**
 * /auth/apple/callback/complete — client-side Apple completion.
 *
 * The sibling `route.ts` receives Apple's form POST and 303-redirects
 * here with `code`, `state`, and optionally a JSON `user` blob in the
 * query string. This page:
 *
 *   1. Parses the values out of the URL.
 *   2. Calls `/auth/apple/complete`.
 *   3. Stores the returned JWT via AuthContext and forwards to
 *      `/for-you`.
 *
 * Apple only sends the `user` payload on the very first sign-in, so we
 * pass it straight through — the backend uses it to seed the display
 * name since the id_token itself doesn't carry one.
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { completeAppleOAuth } from "@/lib/api/auth-identity";
import { useAuth } from "@/lib/auth";

type Status = "pending" | "error";

export default function AppleCompletePage(): JSX.Element {
  return (
    <Suspense fallback={<Shell message="Finishing sign-in…" />}>
      <AppleCompleteInner />
    </Suspense>
  );
}

function Shell({ message }: { message: string }): JSX.Element {
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

function AppleCompleteInner(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();

  const [status, setStatus] = useState<Status>("pending");
  const [message, setMessage] = useState<string>("Finishing sign-in…");
  const hasRun = useRef(false);

  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;

    const providerError = params.get("error");
    const code = params.get("code");
    const state = params.get("state");
    const userRaw = params.get("user");

    if (providerError) {
      setStatus("error");
      setMessage(
        providerError === "user_cancelled_authorize"
          ? "Apple sign-in was cancelled."
          : `Apple returned an error: ${providerError}`,
      );
      return;
    }
    if (!code || !state) {
      setStatus("error");
      setMessage("Missing code or state in callback URL.");
      return;
    }

    let userData: Record<string, unknown> | null = null;
    if (userRaw) {
      try {
        userData = JSON.parse(userRaw) as Record<string, unknown>;
      } catch {
        // If Apple's payload isn't parseable, skip it — it only
        // matters on the first sign-in for the display name.
        userData = null;
      }
    }

    void (async () => {
      try {
        const { token, refresh_token } = await completeAppleOAuth(
          code,
          state,
          userData,
        );
        await login(token, refresh_token);
        router.replace("/for-you");
      } catch (err) {
        setStatus("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "Could not complete Apple sign-in.",
        );
      }
    })();
  }, [params, login, router]);

  if (status === "pending") {
    return <Shell message={message} />;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6">
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 text-center shadow-sm">
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
      </div>
    </main>
  );
}
