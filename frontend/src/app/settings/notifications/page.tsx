/**
 * /settings/notifications — granular email preferences.
 *
 * CSR only. Reads the user's notification_preferences row, then defers
 * every interaction to NotificationPreferencesForm. Loading and error
 * states stay local to this page so the form component is purely
 * presentational once the row is in hand.
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { NotificationPreferencesForm } from "@/components/settings/NotificationPreferencesForm";
import { ApiRequestError } from "@/lib/api/client";
import { getNotificationPreferences } from "@/lib/api/notification-preferences";
import { useRequireAuth } from "@/lib/auth";
import type { NotificationPreferences } from "@/types";

export default function NotificationSettingsPage(): JSX.Element {
  const { token, isLoading, isAuthenticated } = useRequireAuth();
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void getNotificationPreferences(token)
      .then((next) => {
        if (!cancelled) setPrefs(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiRequestError
            ? err.message
            : "Could not load your notification preferences.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (isLoading || !isAuthenticated || !token) {
    return <PageShell>Loading…</PageShell>;
  }

  return (
    <PageShell>
      <div className="mb-6">
        <Link
          href="/settings"
          className="text-xs text-text-secondary underline underline-offset-2"
        >
          ← Back to settings
        </Link>
      </div>
      <h1 className="text-2xl font-semibold text-text-primary">
        Email notifications
      </h1>
      <p className="mt-1 text-sm text-text-secondary">
        Greenroom only sends emails you&apos;d be glad to receive. Tune these
        any time — changes save as you go.
      </p>

      <div className="mt-8">
        {loadError ? (
          <p className="text-sm text-blush-accent" role="alert">
            {loadError}
          </p>
        ) : prefs ? (
          <NotificationPreferencesForm token={token} initial={prefs} />
        ) : (
          <p className="text-sm text-text-secondary">Loading preferences…</p>
        )}
      </div>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }): JSX.Element {
  return <main className="mx-auto max-w-2xl px-6 py-12">{children}</main>;
}
