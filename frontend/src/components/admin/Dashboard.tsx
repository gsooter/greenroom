/**
 * Dashboard — admin landing page summarizing system counts, recent
 * activity, health signals, and the hydration leaderboard.
 *
 * Pulls a single `/admin/dashboard` snapshot and renders four
 * sections. Hydration leaderboard rows expose a "Hydrate" button that
 * mounts the existing HydrationModal — same flow as the artist detail
 * view in the spec.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import HydrationModal from "@/components/admin/HydrationModal";
import {
  AdminApiError,
  AdminCountBreakdown,
  AdminDashboardSnapshot,
  AdminHealthSignal,
  AdminHydrationCandidateArtist,
  AdminLeaderboardArtist,
  getAdminDashboard,
} from "@/lib/api/admin";

interface Props {
  adminKey: string;
  signOut: () => void;
}

const STATUS_BG: Record<AdminHealthSignal["status"], string> = {
  green: "bg-green-soft text-[#1A3D28]",
  yellow: "bg-blush-soft text-[#7A3028]",
  red: "bg-blush-soft text-blush-accent",
};

interface ActiveHydration {
  artistId: string;
  artistName: string;
}

export default function Dashboard({ adminKey, signOut }: Props): JSX.Element {
  const [snap, setSnap] = useState<AdminDashboardSnapshot | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState<ActiveHydration | null>(null);

  const handleAuthError = useCallback(
    (err: unknown): boolean => {
      if (err instanceof AdminApiError && (err.status === 401 || err.status === 403)) {
        signOut();
        return true;
      }
      return false;
    },
    [signOut],
  );

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const next = await getAdminDashboard(adminKey);
      setSnap(next);
    } catch (err) {
      if (handleAuthError(err)) return;
      setError(err instanceof Error ? err.message : "Failed to load dashboard.");
    } finally {
      setLoading(false);
    }
  }, [adminKey, handleAuthError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">
            Admin dashboard
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            System counts, recent activity, health, and hydration leaderboard.
          </p>
        </div>
        <nav className="flex items-center gap-3 text-sm">
          <Link href="/admin" className="text-green-primary underline">
            Scrapers
          </Link>
          <Link href="/admin/users" className="text-green-primary underline">
            Users
          </Link>
          <Link href="/admin/feedback" className="text-green-primary underline">
            Feedback
          </Link>
          <button
            type="button"
            onClick={signOut}
            className="rounded-md border border-border px-3 py-1 text-text-secondary"
          >
            Sign out
          </button>
        </nav>
      </header>

      {loading && (
        <p className="mt-8 text-sm text-text-secondary">Loading dashboard…</p>
      )}
      {error && <p className="mt-8 text-sm text-blush-accent">{error}</p>}

      {snap && (
        <>
          <SystemCounts snap={snap} />
          <RecentActivity snap={snap} />
          <HealthSignals snap={snap} />
          <HydrationLeaderboard
            snap={snap}
            onHydrate={(artist) =>
              setHydrating({ artistId: artist.artist_id, artistName: artist.artist_name })
            }
          />
        </>
      )}

      {hydrating && (
        <HydrationModal
          adminKey={adminKey}
          artistId={hydrating.artistId}
          artistName={hydrating.artistName}
          onClose={() => setHydrating(null)}
          onSuccess={() => {
            void refresh();
          }}
          onAuthError={signOut}
        />
      )}
    </main>
  );
}

function SystemCounts({ snap }: { snap: AdminDashboardSnapshot }): JSX.Element {
  return (
    <section className="mt-8" aria-label="System counts">
      <h2 className="text-lg font-semibold text-text-primary">System counts</h2>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <CountCard
          title="Users"
          breakdown={snap.users}
          href="/admin/users"
          breakdownLabels={{
            active_last_30d: "active 30d",
            signed_in_inactive: "inactive",
            deactivated: "deactivated",
          }}
        />
        <CountCard
          title="Artists"
          breakdown={snap.artists}
          breakdownLabels={{ original: "original", hydrated: "hydrated" }}
        />
        <CountCard
          title="Events"
          breakdown={snap.events}
          breakdownLabels={{
            upcoming: "upcoming",
            past: "past",
            cancelled: "cancelled",
          }}
        />
        <CountCard
          title="Venues"
          breakdown={snap.venues}
          breakdownLabels={{ active: "active", inactive: "inactive" }}
        />
        <SimpleCard
          title="Music connections"
          rows={Object.entries(snap.music_connections).map(([k, v]) => [k, v])}
        />
        <SimpleCard
          title="Push subscriptions"
          rows={[
            ["active", snap.push_subscriptions.active],
            ["disabled", snap.push_subscriptions.disabled],
          ]}
        />
        <SimpleCard
          title="Email-enabled users"
          rows={[["enabled", snap.email_enabled_users]]}
        />
        <SimpleCard
          title="Daily hydration cap"
          rows={[
            ["remaining", snap.daily_hydration_remaining],
            ["of", 100],
          ]}
        />
      </div>
    </section>
  );
}

function CountCard({
  title,
  breakdown,
  breakdownLabels,
  href,
}: {
  title: string;
  breakdown: AdminCountBreakdown;
  breakdownLabels: Record<string, string>;
  href?: string;
}): JSX.Element {
  const inner = (
    <div className="rounded-md border border-border bg-bg-white p-4">
      <p className="text-xs uppercase tracking-wide text-text-secondary">
        {title}
      </p>
      <p className="mt-1 text-2xl font-semibold text-text-primary">
        {breakdown.total.toLocaleString()}
      </p>
      <ul className="mt-2 space-y-0.5 text-xs text-text-secondary">
        {Object.entries(breakdown.breakdown).map(([key, value]) => (
          <li key={key}>
            {breakdownLabels[key] ?? key}: {value.toLocaleString()}
          </li>
        ))}
      </ul>
    </div>
  );
  return href ? (
    <Link href={href} className="block">
      {inner}
    </Link>
  ) : (
    inner
  );
}

function SimpleCard({
  title,
  rows,
}: {
  title: string;
  rows: Array<[string, number]>;
}): JSX.Element {
  return (
    <div className="rounded-md border border-border bg-bg-white p-4">
      <p className="text-xs uppercase tracking-wide text-text-secondary">{title}</p>
      <ul className="mt-2 space-y-0.5 text-sm text-text-primary">
        {rows.map(([k, v]) => (
          <li key={k}>
            <span className="text-text-secondary">{k}:</span>{" "}
            {v.toLocaleString()}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecentActivity({
  snap,
}: {
  snap: AdminDashboardSnapshot;
}): JSX.Element {
  return (
    <section className="mt-8" aria-label="Recent activity">
      <h2 className="text-lg font-semibold text-text-primary">Recent activity</h2>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
        {snap.activity.map((window) => (
          <div
            key={window.label}
            className="rounded-md border border-border bg-bg-white p-4"
          >
            <p className="text-xs uppercase tracking-wide text-text-secondary">
              Last {window.label}
            </p>
            <ul className="mt-2 space-y-1 text-sm text-text-primary">
              <li>
                <span className="text-text-secondary">New users:</span>{" "}
                {window.new_users}
              </li>
              <li>
                <span className="text-text-secondary">New events:</span>{" "}
                {window.new_events}
              </li>
              <li>
                <span className="text-text-secondary">Push sends:</span>{" "}
                {window.push_sends}
              </li>
              <li>
                <span className="text-text-secondary">Email sends:</span>{" "}
                {window.email_sends}
              </li>
              <li>
                <span className="text-text-secondary">Hydrations:</span>{" "}
                {window.hydrations_run} ({window.hydration_artists_added} artists)
              </li>
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function HealthSignals({
  snap,
}: {
  snap: AdminDashboardSnapshot;
}): JSX.Element {
  return (
    <section className="mt-8" aria-label="Health signals">
      <h2 className="text-lg font-semibold text-text-primary">Health</h2>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        {snap.health.map((signal) => (
          <div
            key={signal.label}
            className="rounded-md border border-border bg-bg-white p-4"
          >
            <p className="text-xs uppercase tracking-wide text-text-secondary">
              {signal.label}
            </p>
            <p
              className={`mt-2 inline-block rounded px-2 py-1 text-sm font-medium ${STATUS_BG[signal.status]}`}
            >
              {signal.value}
            </p>
            {signal.detail && (
              <p className="mt-2 text-xs text-text-secondary">{signal.detail}</p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function HydrationLeaderboard({
  snap,
  onHydrate,
}: {
  snap: AdminDashboardSnapshot;
  onHydrate: (artist: AdminHydrationCandidateArtist) => void;
}): JSX.Element {
  return (
    <section className="mt-8" aria-label="Hydration leaderboard">
      <h2 className="text-lg font-semibold text-text-primary">
        Hydration leaderboard
      </h2>
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-md border border-border bg-bg-white p-4">
          <h3 className="text-sm font-semibold text-text-primary">
            Most hydrated (last 30 days)
          </h3>
          {snap.most_hydrated.length === 0 ? (
            <p className="mt-2 text-sm text-text-secondary">
              No hydrations yet.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border">
              {snap.most_hydrated.map((row: AdminLeaderboardArtist) => (
                <li
                  key={row.artist_id}
                  className="flex items-center justify-between py-2 text-sm"
                >
                  <span>{row.artist_name}</span>
                  <span className="text-text-secondary">
                    {row.hydration_count} added
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-md border border-border bg-bg-white p-4">
          <h3 className="text-sm font-semibold text-text-primary">
            Best hydration candidates
          </h3>
          {snap.best_candidates.length === 0 ? (
            <p className="mt-2 text-sm text-text-secondary">
              No eligible candidates yet.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border">
              {snap.best_candidates.map((row) => (
                <li
                  key={row.artist_id}
                  className="flex items-center justify-between gap-3 py-2 text-sm"
                >
                  <div>
                    <p className="font-medium text-text-primary">
                      {row.artist_name}
                    </p>
                    <p className="text-xs text-text-secondary">
                      {row.candidate_count} candidates
                      {row.top_candidate_name
                        ? ` · top: ${row.top_candidate_name}`
                        : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onHydrate(row)}
                    className="rounded-md bg-green-primary px-3 py-1 text-xs font-medium text-text-inverse"
                  >
                    Hydrate
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
