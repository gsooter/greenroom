/**
 * Notification permission prompt — shown only inside the installed
 * PWA, only after the user has done one meaningful action.
 *
 * iOS Safari refuses to even *consider* push permission unless the
 * page is launched from the home screen, so the gate here is
 * deliberately strict: PWA-installed AND signed-in AND the user
 * has demonstrated some intent (saved a show, followed an artist,
 * spent at least 30 seconds on this session). We never auto-prompt
 * the moment the app launches — the OS-level "Allow notifications?"
 * dialog is heavyweight and a stale "no" is hard to recover from.
 *
 * Tapping "Enable" triggers the four-step subscribe flow in
 * src/lib/api/push.ts. Tapping "Not now" stores a 14-day cooldown.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/lib/auth";
import { isAppInstalled } from "@/lib/pwa-detection";
import { enablePush, PushUnavailableError } from "@/lib/api/push";

const DISMISSAL_KEY = "greenroom_push_prompt_dismissed_at";
const ENABLED_KEY = "greenroom_push_enabled";
const COOLDOWN_DAYS = 14;
const MIN_DWELL_MS = 30 * 1000;

function wasRecentlyDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(DISMISSAL_KEY);
    if (!raw) return false;
    const dismissedAt = Number.parseInt(raw, 10);
    if (Number.isNaN(dismissedAt)) return false;
    return Date.now() - dismissedAt < COOLDOWN_DAYS * 24 * 60 * 60 * 1000;
  } catch {
    return false;
  }
}

function alreadyEnabledLocally(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(ENABLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function NotificationPermissionPrompt(): JSX.Element | null {
  const { isAuthenticated, token } = useAuth();
  const [eligible, setEligible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!isAppInstalled()) return;
    if (!isAuthenticated) return;
    if (alreadyEnabledLocally()) return;
    if (wasRecentlyDismissed()) return;
    if (typeof Notification === "undefined") return;
    if (Notification.permission !== "default") return;

    const t = setTimeout(() => setEligible(true), MIN_DWELL_MS);
    return () => clearTimeout(t);
  }, [isAuthenticated]);

  const handleEnable = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await enablePush(token);
      try {
        window.localStorage.setItem(ENABLED_KEY, "1");
      } catch {
        /* ignore */
      }
      setHidden(true);
    } catch (err) {
      // Log to console so the underlying cause is visible in Safari's
      // remote inspector when diagnosing iOS PWA push issues.
      console.error("[push] enable failed", err);
      if (err instanceof PushUnavailableError) {
        setError(err.message);
      } else if (err instanceof Error && err.message) {
        setError(`Could not enable notifications: ${err.message}`);
      } else {
        setError("Could not enable notifications. Try again in a moment.");
      }
    } finally {
      setBusy(false);
    }
  }, [token]);

  const handleDismiss = useCallback(() => {
    setHidden(true);
    try {
      window.localStorage.setItem(DISMISSAL_KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
  }, []);

  if (!eligible || hidden) return null;

  return (
    <div
      role="dialog"
      aria-label="Enable push notifications"
      className="fixed bottom-4 left-4 right-4 z-50 mx-auto max-w-md"
      style={{
        background: "var(--color-bg-white)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "16px",
        boxShadow: "0 12px 32px rgba(26, 40, 32, 0.15)",
      }}
    >
      <p
        className="text-sm font-semibold"
        style={{ color: "var(--color-text-primary)" }}
      >
        Get a ping when your favorites announce DC shows
      </p>
      <p
        className="mt-1 text-xs"
        style={{ color: "var(--color-text-secondary)" }}
      >
        We&apos;ll only ping you when artists you love announce something
        new or a saved show is tomorrow. No marketing.
      </p>
      {error && (
        <p
          className="mt-2 text-xs"
          style={{ color: "var(--color-blush-accent)" }}
        >
          {error}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={handleEnable}
          disabled={busy}
          className="rounded-full px-4 py-2 text-xs font-semibold disabled:opacity-50"
          style={{
            background: "var(--color-green-primary)",
            color: "var(--color-text-inverse)",
          }}
        >
          {busy ? "Enabling…" : "Enable"}
        </button>
        <button
          type="button"
          onClick={handleDismiss}
          disabled={busy}
          className="rounded-full px-4 py-2 text-xs font-semibold"
          style={{
            background: "transparent",
            color: "var(--color-text-secondary)",
          }}
        >
          Not now
        </button>
      </div>
    </div>
  );
}

export default NotificationPermissionPrompt;
