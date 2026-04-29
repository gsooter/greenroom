/**
 * AdminKeyGate — wraps admin pages with a localStorage-backed key prompt.
 *
 * Until a key is set, renders the prompt and nothing else. Once a key
 * is in `localStorage`, calls `children(key, signOut)` so the page can
 * pass the key into every admin API call. On any 401/403 the page can
 * call `signOut()` to clear the stored key and force a re-prompt.
 *
 * The key never leaves the browser — it's read from `localStorage`,
 * sent as `X-Admin-Key` on each fetch, and cleared on logout. It is
 * never embedded in the bundle and never shipped to a server component.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "greenroom.adminKey";

export type AdminGateRender = (
  adminKey: string,
  signOut: () => void,
) => React.ReactNode;

interface Props {
  children: AdminGateRender;
}

export default function AdminKeyGate({ children }: Props): JSX.Element {
  const [adminKey, setAdminKey] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState<boolean>(false);
  const [draft, setDraft] = useState<string>("");

  useEffect(() => {
    try {
      const existing = window.localStorage.getItem(STORAGE_KEY);
      if (existing) setAdminKey(existing);
    } catch {
      /* localStorage unavailable — leave key unset */
    }
    setHydrated(true);
  }, []);

  const signOut = useCallback((): void => {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setAdminKey(null);
    setDraft("");
  }, []);

  const handleSubmit = useCallback(
    (e: React.FormEvent): void => {
      e.preventDefault();
      const trimmed = draft.trim();
      if (!trimmed) return;
      try {
        window.localStorage.setItem(STORAGE_KEY, trimmed);
      } catch {
        /* ignore */
      }
      setAdminKey(trimmed);
    },
    [draft],
  );

  if (!hydrated) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <p className="text-sm text-text-secondary">Loading admin tools…</p>
      </main>
    );
  }

  if (!adminKey) {
    return (
      <main className="mx-auto max-w-md px-4 py-16">
        <h1 className="text-2xl font-semibold text-text-primary">
          Admin sign-in
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          Paste the shared admin key to access scraper and user management.
          The key is stored in this browser only.
        </p>
        <form onSubmit={handleSubmit} className="mt-6 space-y-3">
          <label className="block text-sm font-medium text-text-primary">
            Admin key
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="mt-1 block w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm text-text-primary"
              placeholder="ADMIN_SECRET_KEY"
            />
          </label>
          <button
            type="submit"
            disabled={!draft.trim()}
            className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:opacity-50"
          >
            Sign in
          </button>
        </form>
      </main>
    );
  }

  return <>{children(adminKey, signOut)}</>;
}
