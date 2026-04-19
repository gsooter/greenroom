/**
 * /login — multi-path identity entry.
 *
 * Offers four ways to sign in:
 *   1. Magic link — email address, link mailed via SendGrid.
 *   2. Google OAuth — redirect to Google consent.
 *   3. Apple OAuth — redirect to Apple consent.
 *   4. Passkey (WebAuthn) — disabled for now, pending `py_webauthn`.
 *
 * Spotify is no longer a sign-in option: it is a connected music
 * service surfaced from /settings after the user has an account.
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  requestMagicLink,
  startAppleOAuth,
  startGoogleOAuth,
} from "@/lib/api/auth-identity";
import { useAuth } from "@/lib/auth";

type ProviderStatus = "idle" | "starting" | "error";
type MagicStatus = "idle" | "sending" | "sent" | "error";

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
      <div className="w-full rounded-2xl border border-border bg-bg-surface p-8 shadow-sm">
        <header className="text-center">
          <h1 className="text-2xl font-semibold text-text-primary">
            Sign in to Greenroom
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            One account — pick whichever sign-in is easiest. You can connect
            Spotify later for personalized picks.
          </p>
        </header>

        <MagicLinkForm />

        <Divider>or continue with</Divider>

        <div className="space-y-3">
          <GoogleButton />
          <AppleButton />
          <PasskeyButton />
        </div>

        <p className="mt-6 text-center text-xs text-text-secondary">
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

function MagicLinkForm(): JSX.Element {
  const [email, setEmail] = useState<string>("");
  const [status, setStatus] = useState<MagicStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    setStatus("sending");
    setError(null);
    try {
      await requestMagicLink(trimmed);
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setError(
        err instanceof Error
          ? err.message
          : "Could not send the magic link. Try again.",
      );
    }
  }

  if (status === "sent") {
    return (
      <div
        role="status"
        className="mt-6 rounded-lg border border-green-soft bg-green-soft/40 p-4 text-center text-sm text-text-primary"
      >
        <p className="font-medium">Check your inbox.</p>
        <p className="mt-1 text-text-secondary">
          If {email.trim()} is registered, we just sent a sign-in link. It
          expires in 15 minutes.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="mt-6 space-y-3">
      <label className="block">
        <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
          Email
        </span>
        <input
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="mt-1 w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm text-text-primary"
        />
      </label>
      <button
        type="submit"
        disabled={status === "sending" || email.trim().length === 0}
        className="w-full rounded-lg bg-green-primary px-4 py-3 text-sm font-medium text-text-inverse transition hover:bg-green-dark disabled:cursor-not-allowed disabled:opacity-60"
      >
        {status === "sending" ? "Sending link…" : "Email me a sign-in link"}
      </button>
      {status === "error" && error ? (
        <p className="text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function GoogleButton(): JSX.Element {
  const [status, setStatus] = useState<ProviderStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleClick(): Promise<void> {
    setStatus("starting");
    setError(null);
    try {
      const { authorize_url, state } = await startGoogleOAuth();
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem("greenroom.google_state", state);
      }
      window.location.href = authorize_url;
    } catch (err) {
      setStatus("error");
      setError(
        err instanceof Error
          ? err.message
          : "Could not start Google sign-in.",
      );
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={status === "starting"}
        className="flex w-full items-center justify-center gap-3 rounded-lg border border-border bg-bg-white px-4 py-3 text-sm font-medium text-text-primary transition hover:bg-bg-surface disabled:cursor-not-allowed disabled:opacity-60"
        aria-label="Continue with Google"
      >
        <GoogleGlyph />
        {status === "starting" ? "Redirecting…" : "Continue with Google"}
      </button>
      {status === "error" && error ? (
        <p className="text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

function AppleButton(): JSX.Element {
  const [status, setStatus] = useState<ProviderStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleClick(): Promise<void> {
    setStatus("starting");
    setError(null);
    try {
      const { authorize_url, state } = await startAppleOAuth();
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem("greenroom.apple_state", state);
      }
      window.location.href = authorize_url;
    } catch (err) {
      setStatus("error");
      setError(
        err instanceof Error ? err.message : "Could not start Apple sign-in.",
      );
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={status === "starting"}
        className="flex w-full items-center justify-center gap-3 rounded-lg border border-border bg-green-dark px-4 py-3 text-sm font-medium text-text-inverse transition hover:bg-text-primary disabled:cursor-not-allowed disabled:opacity-60"
        aria-label="Continue with Apple"
      >
        <AppleGlyph />
        {status === "starting" ? "Redirecting…" : "Continue with Apple"}
      </button>
      {status === "error" && error ? (
        <p className="text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

function PasskeyButton(): JSX.Element {
  return (
    <button
      type="button"
      disabled
      className="flex w-full items-center justify-center gap-3 rounded-lg border border-border bg-bg-surface px-4 py-3 text-sm font-medium text-text-secondary opacity-60"
      aria-label="Sign in with a passkey (coming soon)"
      title="Passkey sign-in is coming soon"
    >
      Sign in with a passkey
      <span className="rounded-full bg-bg-white px-2 py-0.5 text-[10px] uppercase tracking-wide">
        Soon
      </span>
    </button>
  );
}

function Divider({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div className="my-6 flex items-center gap-3 text-xs uppercase tracking-wide text-text-secondary">
      <span className="h-px flex-1 bg-border" />
      <span>{children}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

function GoogleGlyph(): JSX.Element {
  return (
    <svg
      aria-hidden
      width="18"
      height="18"
      viewBox="0 0 18 18"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.717v2.259h2.908c1.702-1.567 2.684-3.874 2.684-6.617Z"
        fill="#4285F4"
      />
      <path
        d="M9 18c2.43 0 4.467-.806 5.956-2.183l-2.908-2.26c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A9 9 0 0 0 9 18Z"
        fill="#34A853"
      />
      <path
        d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A9 9 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332Z"
        fill="#FBBC05"
      />
      <path
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A9 9 0 0 0 .957 4.962L3.964 7.294C4.672 5.167 6.656 3.58 9 3.58Z"
        fill="#EA4335"
      />
    </svg>
  );
}

function AppleGlyph(): JSX.Element {
  return (
    <svg
      aria-hidden
      width="16"
      height="18"
      viewBox="0 0 16 18"
      xmlns="http://www.w3.org/2000/svg"
      fill="currentColor"
    >
      <path d="M13.23 9.573c-.022-2.246 1.833-3.325 1.918-3.378-1.046-1.528-2.674-1.737-3.256-1.76-1.386-.141-2.708.817-3.412.817-.718 0-1.798-.797-2.959-.774-1.52.022-2.926.884-3.708 2.246-1.585 2.745-.406 6.804 1.136 9.035.756 1.092 1.654 2.314 2.833 2.271 1.136-.046 1.566-.735 2.938-.735 1.372 0 1.76.735 2.96.712 1.224-.022 2-1.1 2.744-2.198.86-1.262 1.216-2.484 1.238-2.547-.028-.012-2.377-.912-2.402-3.608ZM10.96 3.03c.627-.763 1.05-1.824.935-2.881-.904.037-2 .602-2.646 1.366-.582.68-1.09 1.761-.954 2.8 1.012.078 2.038-.515 2.665-1.286Z" />
    </svg>
  );
}
