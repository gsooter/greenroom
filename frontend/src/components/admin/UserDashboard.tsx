/**
 * UserDashboard — paginated user table with deactivate/reactivate/delete.
 *
 * Hard delete only erases the local Greenroom profile + cascaded rows
 * (saved events, recommendations, music connections). The Knuckles
 * identity record is unaffected — that has to be erased upstream.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  AdminUserSummary,
  PaginatedMeta,
  deactivateAdminUser,
  deleteAdminUser,
  listAdminUsers,
  reactivateAdminUser,
} from "@/lib/api/admin";

interface Props {
  adminKey: string;
  signOut: () => void;
}

const PER_PAGE = 25;

export default function UserDashboard({ adminKey, signOut }: Props): JSX.Element {
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [meta, setMeta] = useState<PaginatedMeta | null>(null);
  const [search, setSearch] = useState<string>("");
  const [active, setActive] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState<number>(1);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

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
      const res = await listAdminUsers(adminKey, {
        search: search.trim() || undefined,
        isActive: active === "" ? undefined : active === "true",
        page,
        perPage: PER_PAGE,
      });
      setUsers(res.users);
      setMeta(res.meta);
    } catch (err) {
      if (handleAuthError(err)) return;
      setError(err instanceof Error ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, [adminKey, search, active, page, handleAuthError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const updateRow = useCallback((updated: AdminUserSummary): void => {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }, []);

  const onDeactivate = useCallback(
    async (id: string): Promise<void> => {
      if (pending) return;
      setPending(id);
      try {
        const updated = await deactivateAdminUser(adminKey, id);
        updateRow(updated);
      } catch (err) {
        if (handleAuthError(err)) return;
        setError(err instanceof Error ? err.message : "Deactivation failed.");
      } finally {
        setPending(null);
      }
    },
    [adminKey, pending, handleAuthError, updateRow],
  );

  const onReactivate = useCallback(
    async (id: string): Promise<void> => {
      if (pending) return;
      setPending(id);
      try {
        const updated = await reactivateAdminUser(adminKey, id);
        updateRow(updated);
      } catch (err) {
        if (handleAuthError(err)) return;
        setError(err instanceof Error ? err.message : "Reactivation failed.");
      } finally {
        setPending(null);
      }
    },
    [adminKey, pending, handleAuthError, updateRow],
  );

  const onDelete = useCallback(
    async (user: AdminUserSummary): Promise<void> => {
      if (pending) return;
      const ok = window.confirm(
        `Hard-delete ${user.email}?\n\n` +
          "This removes the Greenroom profile, saved events, " +
          "recommendations, and music connections. The Knuckles " +
          "identity record is NOT deleted.",
      );
      if (!ok) return;
      setPending(user.id);
      try {
        await deleteAdminUser(adminKey, user.id);
        setUsers((prev) => prev.filter((u) => u.id !== user.id));
      } catch (err) {
        if (handleAuthError(err)) return;
        setError(err instanceof Error ? err.message : "Delete failed.");
      } finally {
        setPending(null);
      }
    },
    [adminKey, pending, handleAuthError],
  );

  const onSubmitSearch = useCallback((e: React.FormEvent): void => {
    e.preventDefault();
    setPage(1);
    void refresh();
  }, [refresh]);

  const totalPages = useMemo(() => {
    if (!meta) return 1;
    return Math.max(1, Math.ceil(meta.total / meta.per_page));
  }, [meta]);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold text-text-primary">Users</h1>
          <p className="text-sm text-text-secondary">
            Greenroom profiles. Knuckles identity records live in the
            identity service.
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

      {error && (
        <div className="rounded-md border border-blush-accent/40 bg-blush-soft px-4 py-3 text-sm text-blush-accent">
          {error}
        </div>
      )}

      <form
        onSubmit={onSubmitSearch}
        className="flex flex-wrap items-end gap-3"
      >
        <label className="text-sm text-text-secondary">
          Search
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="email or name"
            className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
          />
        </label>
        <label className="text-sm text-text-secondary">
          Active
          <select
            value={active}
            onChange={(e) => {
              setPage(1);
              setActive(e.target.value as "" | "true" | "false");
            }}
            className="ml-2 rounded-md border border-border bg-bg-white px-2 py-1 text-sm text-text-primary"
          >
            <option value="">All</option>
            <option value="true">Active</option>
            <option value="false">Deactivated</option>
          </select>
        </label>
        <button
          type="submit"
          className="rounded-md border border-border px-3 py-1 text-sm text-text-primary hover:bg-bg-surface"
        >
          Apply
        </button>
      </form>

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="min-w-full divide-y divide-border bg-bg-white text-sm">
          <thead className="bg-bg-surface text-left text-xs uppercase tracking-wide text-text-secondary">
            <tr>
              <th className="px-3 py-2">Email</th>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Connections</th>
              <th className="px-3 py-2">Last login</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {loading && users.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-text-secondary">
                  Loading users…
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-text-secondary">
                  No users match the current filters.
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.id}>
                  <td className="px-3 py-2 font-medium text-text-primary">
                    {u.email}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {u.display_name ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        u.is_active
                          ? "bg-green-soft text-[#1A3D28]"
                          : "bg-bg-surface text-text-secondary"
                      }`}
                    >
                      {u.is_active ? "active" : "deactivated"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {u.music_connections.length === 0
                      ? "—"
                      : u.music_connections.join(", ")}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDateTime(u.last_login_at)}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDateTime(u.created_at)}
                  </td>
                  <td className="px-3 py-2 text-right space-x-2">
                    {u.is_active ? (
                      <button
                        type="button"
                        disabled={pending !== null}
                        onClick={() => void onDeactivate(u.id)}
                        className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-surface disabled:opacity-50"
                      >
                        Deactivate
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={pending !== null}
                        onClick={() => void onReactivate(u.id)}
                        className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-surface disabled:opacity-50"
                      >
                        Reactivate
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={pending !== null}
                      onClick={() => void onDelete(u)}
                      className="rounded-md bg-blush-accent px-2 py-1 text-xs font-medium text-text-inverse disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {meta && (
        <div className="flex items-center justify-between text-sm text-text-secondary">
          <span>
            {meta.total} user{meta.total === 1 ? "" : "s"} · page {meta.page} of{" "}
            {totalPages}
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
      )}
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
