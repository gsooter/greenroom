/**
 * FeedbackDashboard — paginated table of in-app beta feedback.
 *
 * Mirrors the UserDashboard pattern: filter row, table, pagination,
 * mark-as-resolved action. Slack already shows submissions in real
 * time; this view is for triage and historical lookup.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  type AdminFeedback,
  type AdminFeedbackKind,
  type PaginatedMeta,
  listAdminFeedback,
  setAdminFeedbackResolved,
} from "@/lib/api/admin";

interface Props {
  adminKey: string;
  signOut: () => void;
}

const PER_PAGE = 25;

const KIND_LABELS: Record<AdminFeedbackKind, string> = {
  bug: "🐞 Bug",
  feature: "✨ Feature",
  general: "💬 General",
};

export default function FeedbackDashboard({
  adminKey,
  signOut,
}: Props): JSX.Element {
  const [items, setItems] = useState<AdminFeedback[]>([]);
  const [meta, setMeta] = useState<PaginatedMeta | null>(null);
  const [kind, setKind] = useState<"" | AdminFeedbackKind>("");
  const [resolved, setResolved] = useState<"" | "true" | "false">("false");
  const [page, setPage] = useState<number>(1);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const handleAuthError = useCallback(
    (err: unknown): boolean => {
      if (
        err instanceof AdminApiError &&
        (err.status === 401 || err.status === 403)
      ) {
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
      const res = await listAdminFeedback(adminKey, {
        kind: kind === "" ? undefined : kind,
        isResolved: resolved === "" ? undefined : resolved === "true",
        page,
        perPage: PER_PAGE,
      });
      setItems(res.feedback);
      setMeta(res.meta);
    } catch (err) {
      if (handleAuthError(err)) return;
      setError(err instanceof Error ? err.message : "Failed to load feedback.");
    } finally {
      setLoading(false);
    }
  }, [adminKey, kind, resolved, page, handleAuthError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onToggleResolved = useCallback(
    async (row: AdminFeedback): Promise<void> => {
      if (pending) return;
      setPending(row.id);
      try {
        const updated = await setAdminFeedbackResolved(
          adminKey,
          row.id,
          !row.is_resolved,
        );
        setItems((prev) =>
          prev.map((item) => (item.id === updated.id ? updated : item)),
        );
      } catch (err) {
        if (handleAuthError(err)) return;
        setError(err instanceof Error ? err.message : "Update failed.");
      } finally {
        setPending(null);
      }
    },
    [adminKey, pending, handleAuthError],
  );

  const totalPages = useMemo(() => {
    if (!meta) return 1;
    return Math.max(1, Math.ceil(meta.total / meta.per_page));
  }, [meta]);

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold text-text-primary">
            Feedback
          </h1>
          <p className="text-sm text-text-secondary">
            Submissions from the in-app beta widget. Slack also gets a
            real-time copy.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/admin"
            className="rounded-md border border-border px-3 py-2 text-sm font-medium text-text-primary hover:bg-bg-surface"
          >
            Back to scrapers
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

      {error ? (
        <div className="rounded-md border border-blush-accent/40 bg-blush-soft px-4 py-3 text-sm text-blush-accent">
          {error}
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm text-text-secondary">
          Type
          <select
            value={kind}
            onChange={(event) => {
              setPage(1);
              setKind(event.target.value as "" | AdminFeedbackKind);
            }}
            className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
          >
            <option value="">All</option>
            <option value="bug">Bug</option>
            <option value="feature">Feature</option>
            <option value="general">General</option>
          </select>
        </label>
        <label className="text-sm text-text-secondary">
          Resolved
          <select
            value={resolved}
            onChange={(event) => {
              setPage(1);
              setResolved(event.target.value as "" | "true" | "false");
            }}
            className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
          >
            <option value="">All</option>
            <option value="false">Open</option>
            <option value="true">Resolved</option>
          </select>
        </label>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="min-w-full divide-y divide-border bg-bg-white text-sm">
          <thead className="bg-bg-surface text-left text-xs uppercase tracking-wide text-text-secondary">
            <tr>
              <th className="px-3 py-2">Submitted</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">From</th>
              <th className="px-3 py-2">Message</th>
              <th className="px-3 py-2">Page</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {loading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-6 text-center text-text-secondary"
                >
                  Loading feedback…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-6 text-center text-text-secondary"
                >
                  No feedback matches the current filters.
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} className="align-top">
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDateTime(item.created_at)}
                  </td>
                  <td className="px-3 py-2 text-text-primary">
                    {KIND_LABELS[item.kind]}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {item.email ?? (
                      <span className="italic">anonymous</span>
                    )}
                  </td>
                  <td className="max-w-xl whitespace-pre-wrap break-words px-3 py-2 text-text-primary">
                    {item.message}
                  </td>
                  <td className="px-3 py-2 text-xs text-text-secondary">
                    {item.page_url ? (
                      <a
                        href={item.page_url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2 hover:text-text-primary"
                      >
                        {shortenUrl(item.page_url)}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        item.is_resolved
                          ? "bg-green-soft text-[#1A3D28]"
                          : "bg-bg-surface text-text-secondary"
                      }`}
                    >
                      {item.is_resolved ? "resolved" : "open"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      disabled={pending !== null}
                      onClick={() => void onToggleResolved(item)}
                      className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-surface disabled:opacity-50"
                    >
                      {item.is_resolved ? "Reopen" : "Mark resolved"}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {meta ? (
        <div className="flex items-center justify-between text-sm text-text-secondary">
          <span>
            {meta.total} submission{meta.total === 1 ? "" : "s"} · page{" "}
            {meta.page} of {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="rounded-md border border-border px-3 py-1 text-text-primary hover:bg-bg-surface disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => p + 1)}
              disabled={!meta.has_next || loading}
              className="rounded-md border border-border px-3 py-1 text-text-primary hover:bg-bg-surface disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function shortenUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}` || parsed.host;
  } catch {
    return url.length > 40 ? `${url.slice(0, 40)}…` : url;
  }
}
