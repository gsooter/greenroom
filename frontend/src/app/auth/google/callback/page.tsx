/**
 * /auth/google/callback — Google OAuth landing page.
 *
 * Google redirects here with `?code=...&state=...`. We call
 * `/auth/google/complete` to exchange them for a Greenroom JWT, persist
 * the token via AuthContext, and forward the user to `/for-you`.
 *
 * This is a client page (not a route handler) because the login step
 * needs access to localStorage, which only runs in the browser.
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { completeGoogleOAuth } from "@/lib/api/auth-identity";
import { useAuth } from "@/lib/auth";
import { resolvePostAuthDestination } from "@/lib/welcome-redirect";

type Status = "pending" | "error";

export default function GoogleCallbackPage(): JSX.Element {
  return (
    <Suspense fallback={<Shell message="Finishing sign-in…" />}>
      <GoogleCallbackInner />
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

function GoogleCallbackInner(): JSX.Element {
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

    if (providerError) {
      setStatus("error");
      setMessage(
        providerError === "access_denied"
          ? "Google sign-in was cancelled."
          : `Google returned an error: ${providerError}`,
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
        const { token, refresh_token } = await completeGoogleOAuth(code, state);
        await login(token, refresh_token);
        router.replace(await resolvePostAuthDestination(token));
      } catch (err) {
        setStatus("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "Could not complete Google sign-in.",
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
