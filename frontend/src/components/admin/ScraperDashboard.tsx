/**
 * ScraperDashboard — fleet summary + recent runs table + per-venue Run-now.
 *
 * Calls `/api/v1/admin/scrapers` for the fleet header and
 * `/api/v1/admin/scraper-runs` for the run history. Trigger buttons
 * POST to `/api/v1/admin/scrapers/{slug}/run` and refresh the run list
 * after the synchronous scrape returns.
 *
 * On 401/403 the page calls `signOut()` so the gate clears the stored
 * key and prompts again.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  AdminFleetSummary,
  AdminFleetVenue,
  AdminScraperRun,
  AdminTestAlertResult,
  getFleetSummary,
  listScraperRuns,
  sendTestAlert,
  triggerScraperRun,
} from "@/lib/api/admin";

interface Props {
  adminKey: string;
  signOut: () => void;
}

type StatusFilter = "" | "success" | "partial" | "failed";

const STATUS_STYLES: Record<AdminScraperRun["status"], string> = {
  success: "bg-green-soft text-[#1A3D28]",
  partial: "bg-blush-soft text-[#7A3028]",
  failed: "bg-blush-soft text-blush-accent",
};

export default function ScraperDashboard({ adminKey, signOut }: Props): JSX.Element {
  const [fleet, setFleet] = useState<AdminFleetSummary | null>(null);
  const [runs, setRuns] = useState<AdminScraperRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [venueFilter, setVenueFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [triggering, setTriggering] = useState<string | null>(null);
  const [testingAlert, setTestingAlert] = useState<boolean>(false);
  const [alertResult, setAlertResult] = useState<AdminTestAlertResult | null>(null);

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
      const [fleetRes, runsRes] = await Promise.all([
        getFleetSummary(adminKey),
        listScraperRuns(adminKey, {
          venueSlug: venueFilter || undefined,
          status: statusFilter || undefined,
          page: 1,
          perPage: 50,
        }),
      ]);
      setFleet(fleetRes);
      setRuns(runsRes.runs);
    } catch (err) {
      if (handleAuthError(err)) return;
      setError(err instanceof Error ? err.message : "Failed to load admin data.");
    } finally {
      setLoading(false);
    }
  }, [adminKey, venueFilter, statusFilter, handleAuthError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSendTestAlert = useCallback(async (): Promise<void> => {
    if (testingAlert) return;
    setTestingAlert(true);
    setAlertResult(null);
    try {
      const result = await sendTestAlert(adminKey);
      setAlertResult(result);
    } catch (err) {
      if (handleAuthError(err)) return;
      setError(
        err instanceof Error
          ? `Test alert failed: ${err.message}`
          : "Test alert failed.",
      );
    } finally {
      setTestingAlert(false);
    }
  }, [adminKey, handleAuthError, testingAlert]);

  const onTrigger = useCallback(
    async (slug: string): Promise<void> => {
      if (triggering) return;
      setTriggering(slug);
      try {
        await triggerScraperRun(adminKey, slug);
        await refresh();
      } catch (err) {
        if (handleAuthError(err)) return;
        setError(
          err instanceof Error
            ? `Run failed: ${err.message}`
            : "Run failed.",
        );
      } finally {
        setTriggering(null);
      }
    },
    [adminKey, refresh, handleAuthError, triggering],
  );

  const venues = useMemo<AdminFleetVenue[]>(
    () => fleet?.venues ?? [],
    [fleet],
  );

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 space-y-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-text-primary">Admin</h1>
          <p className="text-sm text-text-secondary">
            Scraper fleet, run history, and user management.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void onSendTestAlert()}
            disabled={testingAlert}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium text-text-primary hover:bg-bg-surface disabled:opacity-50"
          >
            {testingAlert ? "Sending…" : "Send test alert"}
          </button>
          <Link
            href="/admin/users"
            className="rounded-md border border-border px-3 py-2 text-sm font-medium text-text-primary hover:bg-bg-surface"
          >
            Manage users
          </Link>
          <button
            type="button"
            onClick={signOut}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium text-text-secondary hover:bg-bg-surface"
          >
            Sign out
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-blush-accent/40 bg-blush-soft px-4 py-3 text-sm text-blush-accent">
          {error}
        </div>
      )}

      {alertResult && (
        <TestAlertBanner result={alertResult} onDismiss={() => setAlertResult(null)} />
      )}

      <FleetSummary fleet={fleet} />

      <section className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-text-primary">
              Recent runs
            </h2>
            <p className="text-sm text-text-secondary">
              Newest first. Trigger a manual run from the venues table below.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-sm text-text-secondary">
              Venue
              <select
                value={venueFilter}
                onChange={(e) => setVenueFilter(e.target.value)}
                className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
              >
                <option value="">All</option>
                {venues.map((v) => (
                  <option key={v.slug} value={v.slug}>
                    {v.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm text-text-secondary">
              Status
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
              >
                <option value="">All</option>
                <option value="success">Success</option>
                <option value="partial">Partial</option>
                <option value="failed">Failed</option>
              </select>
            </label>
            <button
              type="button"
              onClick={() => void refresh()}
              className="rounded-md border border-border px-3 py-1 text-sm text-text-primary hover:bg-bg-surface"
            >
              Refresh
            </button>
          </div>
        </div>

        <RunsTable runs={runs} loading={loading} />
      </section>

      <VenuesTable
        venues={venues}
        triggering={triggering}
        onTrigger={onTrigger}
      />
    </main>
  );
}

function TestAlertBanner({
  result,
  onDismiss,
}: {
  result: AdminTestAlertResult;
  onDismiss: () => void;
}): JSX.Element {
  const channels: string[] = [];
  if (result.slack_configured) channels.push("Slack");
  if (result.email_configured) channels.push("email");
  const channelText =
    channels.length > 0 ? channels.join(" + ") : "no channels (check env vars)";
  const tone =
    channels.length === 0
      ? "border-blush-accent/40 bg-blush-soft text-blush-accent"
      : "border-green-primary/30 bg-green-soft text-[#1A3D28]";
  return (
    <div
      className={`flex items-start justify-between gap-4 rounded-md border px-4 py-3 text-sm ${tone}`}
    >
      <div>
        <p className="font-medium">
          {result.delivered ? "Test alert dispatched." : "Test alert suppressed."}
        </p>
        <p className="text-xs">
          Severity {result.severity} • channels: {channelText}
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-xs underline-offset-2 hover:underline"
      >
        Dismiss
      </button>
    </div>
  );
}

function FleetSummary({ fleet }: { fleet: AdminFleetSummary | null }): JSX.Element {
  if (!fleet) {
    return (
      <section className="rounded-md border border-border bg-bg-surface px-4 py-3 text-sm text-text-secondary">
        Loading fleet summary…
      </section>
    );
  }
  const regions = Object.entries(fleet.by_region).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  return (
    <section className="rounded-md border border-border bg-bg-white px-4 py-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <span className="text-sm text-text-secondary">
          Enabled venues:{" "}
          <span className="font-semibold text-text-primary">{fleet.enabled}</span>
        </span>
        {regions.map(([region, count]) => (
          <span key={region} className="text-sm text-text-secondary">
            {region}:{" "}
            <span className="font-semibold text-text-primary">{count}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

function RunsTable({
  runs,
  loading,
}: {
  runs: AdminScraperRun[];
  loading: boolean;
}): JSX.Element {
  if (loading && runs.length === 0) {
    return (
      <p className="rounded-md border border-border bg-bg-surface px-4 py-3 text-sm text-text-secondary">
        Loading runs…
      </p>
    );
  }
  if (runs.length === 0) {
    return (
      <p className="rounded-md border border-border bg-bg-surface px-4 py-3 text-sm text-text-secondary">
        No runs match the current filters.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="min-w-full divide-y divide-border bg-bg-white text-sm">
        <thead className="bg-bg-surface text-left text-xs uppercase tracking-wide text-text-secondary">
          <tr>
            <th className="px-3 py-2">Venue</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Events</th>
            <th className="px-3 py-2">Duration</th>
            <th className="px-3 py-2">Started</th>
            <th className="px-3 py-2">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="px-3 py-2 font-medium text-text-primary">
                {r.venue_slug}
              </td>
              <td className="px-3 py-2">
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                    STATUS_STYLES[r.status]
                  }`}
                >
                  {r.status}
                </span>
              </td>
              <td className="px-3 py-2 text-text-primary">{r.event_count}</td>
              <td className="px-3 py-2 text-text-secondary">
                {r.duration_seconds === null
                  ? "—"
                  : `${r.duration_seconds.toFixed(1)}s`}
              </td>
              <td className="px-3 py-2 text-text-secondary">
                {formatDateTime(r.started_at)}
              </td>
              <td className="px-3 py-2 text-text-secondary">
                {r.error_message ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VenuesTable({
  venues,
  triggering,
  onTrigger,
}: {
  venues: AdminFleetVenue[];
  triggering: string | null;
  onTrigger: (slug: string) => Promise<void>;
}): JSX.Element {
  if (venues.length === 0) return <></>;
  const sorted = [...venues].sort((a, b) =>
    a.region.localeCompare(b.region) || a.display_name.localeCompare(b.display_name),
  );
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold text-text-primary">Venues</h2>
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="min-w-full divide-y divide-border bg-bg-white text-sm">
          <thead className="bg-bg-surface text-left text-xs uppercase tracking-wide text-text-secondary">
            <tr>
              <th className="px-3 py-2">Venue</th>
              <th className="px-3 py-2">Region</th>
              <th className="px-3 py-2">City</th>
              <th className="px-3 py-2">Scraper</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sorted.map((v) => (
              <tr key={v.slug}>
                <td className="px-3 py-2 font-medium text-text-primary">
                  {v.display_name}
                </td>
                <td className="px-3 py-2 text-text-secondary">{v.region}</td>
                <td className="px-3 py-2 text-text-secondary">{v.city_slug}</td>
                <td className="px-3 py-2 text-text-secondary">
                  {v.scraper_class.split(".").slice(-1)[0]}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => void onTrigger(v.slug)}
                    disabled={triggering !== null}
                    className="rounded-md bg-green-primary px-3 py-1 text-xs font-medium text-text-inverse disabled:opacity-50"
                  >
                    {triggering === v.slug ? "Running…" : "Run now"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
