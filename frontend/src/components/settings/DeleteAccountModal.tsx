/**
 * Confirmation modal for the /settings danger zone.
 *
 * The user must type the literal word ``delete`` (case-insensitive)
 * before the destructive button enables. This guard exists because
 * deactivation revokes every active session and we don't want a stray
 * misclick to log someone out — the typed gate is much harder to fire
 * accidentally than a single confirm dialog.
 */

"use client";

import { useEffect, useState } from "react";

interface Props {
  email: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

const REQUIRED_WORD = "delete";

/**
 * Render the deactivate-account confirmation dialog.
 *
 * Args:
 *     email: The signed-in email address, shown so the user can double-
 *         check the account they're about to deactivate.
 *     busy: When ``True``, the destructive button shows a spinner label
 *         and ignores further clicks.
 *     error: API failure message to display inline, or ``null``.
 *     onCancel: Called when the user dismisses (close button, backdrop,
 *         or ``Escape``).
 *     onConfirm: Called once the user has typed the required word and
 *         clicked the destructive button.
 *
 * Returns:
 *     A modal dialog mounted via fixed positioning over the page.
 */
export function DeleteAccountModal({
  email,
  busy,
  error,
  onCancel,
  onConfirm,
}: Props): JSX.Element {
  const [typed, setTyped] = useState<string>("");
  const matches = typed.trim().toLowerCase() === REQUIRED_WORD;

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onCancel]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-account-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-text-primary/50 px-4"
    >
      <button
        type="button"
        aria-label="Close dialog"
        onClick={onCancel}
        className="absolute inset-0 cursor-default"
      />
      <div className="relative w-full max-w-md rounded-xl bg-bg-white p-6 shadow-xl">
        <h2
          id="delete-account-title"
          className="text-lg font-semibold text-text-primary"
        >
          Deactivate account
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          You&apos;re about to deactivate{" "}
          <span className="font-medium text-text-primary">{email}</span>. Saved
          shows stay linked for analytics, but every active session is logged
          out and the account is locked. Reach support to reactivate.
        </p>
        <label className="mt-5 block">
          <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
            Type{" "}
            <span className="font-mono normal-case text-text-primary">
              delete
            </span>{" "}
            to confirm
          </span>
          <input
            type="text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoFocus
            autoComplete="off"
            className="mt-1 w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm focus:border-blush-accent focus:outline-none focus:ring-1 focus:ring-blush-accent"
          />
        </label>
        {error ? (
          <p className="mt-3 text-xs text-blush-accent" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary transition hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!matches || busy}
            className="rounded-md bg-blush-accent px-4 py-2 text-sm font-medium text-text-inverse transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Deactivating…" : "Deactivate account"}
          </button>
        </div>
      </div>
    </div>
  );
}
