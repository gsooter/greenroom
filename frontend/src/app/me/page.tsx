/**
 * /me — consolidated authenticated dashboard.
 *
 * CSR-only — every section here is per-user, so there is nothing to SSR.
 * The thin page wrapper handles auth/onboarding gating and hands the
 * session token to ``MeDashboard``, which owns the actual layout and
 * data fetching for picks, saved shows, follows, and the account
 * actions.
 */

"use client";

import MeDashboard from "@/components/me/MeDashboard";
import { useRequireOnboarded } from "@/lib/auth";

export default function MePage(): JSX.Element {
  const { user, isAuthenticated, isLoading, token } = useRequireOnboarded();

  if (isLoading || !isAuthenticated || !user || !token) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-12">
        <p className="text-sm text-text-secondary">Loading your account…</p>
      </main>
    );
  }

  const displayName =
    user.display_name?.trim() || user.email.split("@")[0] || "you";

  return <MeDashboard displayName={displayName} token={token} />;
}
