/**
 * Install prompt — encourages engaged mobile users to add Greenroom
 * to their home screen.
 *
 * Behavior is deliberately conservative: this banner shows once per
 * session, only after the user has spent at least 60 seconds and
 * navigated to two pages, only on a mobile install-capable browser,
 * and only when the user is signed in. Dismissal is tracked in
 * localStorage with a 7-day cooldown so a "no" stays "no" without
 * us pestering on every visit.
 *
 * On Android Chrome the "Add" button calls `prompt()` on the
 * captured `beforeinstallprompt` event, which surfaces the native
 * install dialog. On iOS Safari we render manual instructions
 * pointing at the share sheet — Apple has explicitly chosen not to
 * expose a programmatic install API, so there is no alternative.
 */

"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import {
  isAppInstalled,
  isMobileBrowserInstallable,
  isMobileSafari,
  type BeforeInstallPromptEvent,
} from "@/lib/pwa-detection";

const DISMISSAL_KEY = "greenroom_install_prompt_dismissed_at";
const COOLDOWN_DAYS = 7;
const MIN_DWELL_MS = 60 * 1000;
const MIN_PAGE_VIEWS = 2;

type Platform = "ios" | "android";

function wasRecentlyDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(DISMISSAL_KEY);
    if (!raw) return false;
    const dismissedAt = Number.parseInt(raw, 10);
    if (Number.isNaN(dismissedAt)) return false;
    const ageMs = Date.now() - dismissedAt;
    return ageMs < COOLDOWN_DAYS * 24 * 60 * 60 * 1000;
  } catch {
    // localStorage can throw in private mode on Safari — treat as
    // "not dismissed" so the prompt can still surface, but quietly.
    return false;
  }
}

export function InstallPrompt(): JSX.Element | null {
  const { isAuthenticated } = useAuth();
  const pathname = usePathname();

  const [platform, setPlatform] = useState<Platform | null>(null);
  const [eligible, setEligible] = useState(false);
  const [dismissedThisSession, setDismissedThisSession] = useState(false);
  const installEventRef = useRef<BeforeInstallPromptEvent | null>(null);
  const pageViewsRef = useRef(0);
  const dwellTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dwellElapsedRef = useRef(false);

  // Increment page-view counter on every navigation. Counting unique
  // pathnames avoids bumping for in-page hash changes.
  const lastPathRef = useRef<string | null>(null);
  useEffect(() => {
    if (lastPathRef.current !== pathname) {
      pageViewsRef.current += 1;
      lastPathRef.current = pathname;
    }
  }, [pathname]);

  // One-time setup: detect platform, start dwell timer, capture
  // beforeinstallprompt on Android. Runs once on mount because none
  // of the inputs change for the life of the page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isAppInstalled()) return;
    if (!isMobileBrowserInstallable()) return;
    if (wasRecentlyDismissed()) return;

    setPlatform(isMobileSafari() ? "ios" : "android");

    dwellTimerRef.current = setTimeout(() => {
      dwellElapsedRef.current = true;
      maybeShow();
    }, MIN_DWELL_MS);

    const onBeforeInstall = (event: Event) => {
      event.preventDefault();
      installEventRef.current = event as BeforeInstallPromptEvent;
      maybeShow();
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstall);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      if (dwellTimerRef.current !== null) {
        clearTimeout(dwellTimerRef.current);
      }
    };
    // Intentionally empty deps — captures should bind once per page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-evaluate eligibility whenever auth state or path change. The
  // dwell timer and beforeinstallprompt callback also call this, so
  // the gate fires whichever signal arrives last.
  const maybeShow = useCallback(() => {
    if (!isAuthenticated) return;
    if (dismissedThisSession) return;
    if (!dwellElapsedRef.current) return;
    if (pageViewsRef.current < MIN_PAGE_VIEWS) return;
    setEligible(true);
  }, [isAuthenticated, dismissedThisSession]);

  useEffect(() => {
    maybeShow();
  }, [pathname, isAuthenticated, maybeShow]);

  const handleDismiss = useCallback(() => {
    setDismissedThisSession(true);
    setEligible(false);
    try {
      window.localStorage.setItem(DISMISSAL_KEY, String(Date.now()));
    } catch {
      // Same private-mode caveat as wasRecentlyDismissed — silently
      // swallow so the dismiss UI still works for the current session.
    }
  }, []);

  const handleAdd = useCallback(async () => {
    const captured = installEventRef.current;
    if (!captured) return;
    try {
      await captured.prompt();
      const choice = await captured.userChoice;
      if (choice.outcome === "accepted") {
        setEligible(false);
      }
    } finally {
      installEventRef.current = null;
    }
  }, []);

  if (!eligible || platform === null) return null;

  return (
    <div
      role="dialog"
      aria-label="Install Greenroom to your home screen"
      className="fixed bottom-4 left-4 right-4 z-50 mx-auto max-w-md"
      style={{
        background: "rgba(255, 255, 255, 0.92)",
        backdropFilter: "saturate(180%) blur(20px)",
        WebkitBackdropFilter: "saturate(180%) blur(20px)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "14px 16px",
        boxShadow: "0 12px 32px rgba(26, 40, 32, 0.15)",
      }}
    >
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss install prompt"
        className="absolute right-2 top-2 grid h-7 w-7 place-items-center rounded-full"
        style={{
          color: "var(--color-text-secondary)",
          background: "transparent",
          fontSize: "16px",
          lineHeight: 1,
        }}
      >
        ×
      </button>
      <div className="flex items-start gap-3 pr-6">
        <div
          aria-hidden
          className="grid h-10 w-10 shrink-0 place-items-center rounded-xl"
          style={{ background: "var(--color-green-dark)" }}
        >
          <span
            className="block h-3 w-3 rounded-full"
            style={{ background: "var(--color-green-soft)" }}
          />
        </div>
        <div className="flex-1">
          {platform === "android" ? (
            <AndroidBody onAdd={handleAdd} canAdd={!!installEventRef.current} />
          ) : (
            <IosBody />
          )}
        </div>
      </div>
    </div>
  );
}

function AndroidBody({
  onAdd,
  canAdd,
}: {
  onAdd: () => void;
  canAdd: boolean;
}): JSX.Element {
  return (
    <>
      <p
        className="text-sm font-semibold"
        style={{ color: "var(--color-text-primary)" }}
      >
        Add Greenroom to your home screen
      </p>
      <p
        className="mt-1 text-xs"
        style={{ color: "var(--color-text-secondary)" }}
      >
        Faster access and tour alerts the moment your favorites
        announce.
      </p>
      <div className="mt-3">
        <button
          type="button"
          onClick={onAdd}
          disabled={!canAdd}
          className="rounded-full px-4 py-2 text-xs font-semibold disabled:opacity-50"
          style={{
            background: "var(--color-green-primary)",
            color: "var(--color-text-inverse)",
          }}
        >
          Add to home screen
        </button>
      </div>
    </>
  );
}

function IosBody(): JSX.Element {
  return (
    <>
      <p
        className="text-sm font-semibold"
        style={{ color: "var(--color-text-primary)" }}
      >
        Add Greenroom to your home screen
      </p>
      <p
        className="mt-1 text-xs"
        style={{ color: "var(--color-text-secondary)" }}
      >
        Tap the share icon{" "}
        <ShareIcon />
        {" "}then choose <strong>Add to Home Screen</strong>.
      </p>
    </>
  );
}

function ShareIcon(): JSX.Element {
  // Inline SVG so the icon ships with the component and works in
  // dark/light without a separate asset. Stroke uses currentColor
  // so it inherits the surrounding text color.
  return (
    <svg
      role="img"
      aria-label="iOS share icon"
      viewBox="0 0 16 20"
      width="14"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      style={{ verticalAlign: "-3px", display: "inline-block" }}
    >
      <path d="M8 1v12" strokeLinecap="round" />
      <path d="M4 5l4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M3 9H2a1 1 0 00-1 1v8a1 1 0 001 1h12a1 1 0 001-1v-8a1 1 0 00-1-1h-1"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default InstallPrompt;
