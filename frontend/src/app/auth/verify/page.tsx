/**
 * /auth/verify — magic-link completion page.
 *
 * The email Resend delivers points here with `?token=...`. We POST
 * that token to `/auth/magic-link/verify`, store the returned JWT via
 * AuthContext, and send the user to `/for-you`.
 *
 * The magic-link handoff is strictly browser-side: the backend issues
 * a JWT which AuthContext.login persists to localStorage, a capability
 * unavailable in a server component.
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { verifyMagicLink } from "@/lib/api/auth-identity";
import { useAuth } from "@/lib/auth";
import { resolvePostAuthDestination } from "@/lib/welcome-redirect";

type Status = "pending" | "error";

export default function MagicLinkVerifyPage(): JSX.Element {
  return (
    <Suspense fallback={<Shell message="Finishing sign-in…" />}>
      <MagicLinkVerifyInner />
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

function MagicLinkVerifyInner(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();

  const [status, setStatus] = useState<Status>("pending");
  const [message, setMessage] = useState<string>("Finishing sign-in…");
  const hasRun = useRef(false);

  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;

    const token = params.get("token");
    if (!token) {
      setStatus("error");
      setMessage("The sign-in link is missing its token.");
      return;
    }

    void (async () => {
      try {
        const { token: jwt, refresh_token } = await verifyMagicLink(token);
        await login(jwt, refresh_token);
        router.replace(await resolvePostAuthDestination(jwt));
      } catch (err) {
        setStatus("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "This sign-in link is no longer valid.",
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
          Sign-in link didn&apos;t work
        </h1>
        <p className="mt-2 text-sm text-blush-accent" role="alert">
          {message}
        </p>
        <Link
          href="/login"
          className="mt-4 inline-block rounded-lg bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse hover:bg-green-dark"
        >
          Request a new link
        </Link>
      </div>
    </main>
  );
}
