/**
 * HydrationModal — confirmation modal for the artist hydration flow.
 *
 * Mounts when an admin clicks "Hydrate similar artists". Renders the
 * preview the backend returned, lets the operator deselect candidates,
 * and dispatches the execute call when the operator confirms.
 *
 * The operator's email is required because the audit log captures it
 * verbatim — we don't have an admin identity from the X-Admin-Key
 * gating, so the operator types it. localStorage caches the value so
 * the same operator isn't prompted on every hydration.
 */

"use client";

import { useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  AdminHydrationCandidate,
  AdminHydrationPreview,
  AdminHydrationResult,
  executeHydration,
  getHydrationPreview,
} from "@/lib/api/admin";

const ADMIN_EMAIL_STORAGE_KEY = "greenroom.adminEmail";

interface Props {
  adminKey: string;
  artistId: string;
  artistName: string;
  onClose: () => void;
  onSuccess?: (result: AdminHydrationResult) => void;
  onAuthError: () => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; preview: AdminHydrationPreview }
  | { kind: "error"; message: string };

export default function HydrationModal({
  adminKey,
  artistId,
  artistName,
  onClose,
  onSuccess,
  onAuthError,
}: Props): JSX.Element {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [adminEmail, setAdminEmail] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [result, setResult] = useState<AdminHydrationResult | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const cached = window.localStorage.getItem(ADMIN_EMAIL_STORAGE_KEY);
      if (cached) setAdminEmail(cached);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const preview = await getHydrationPreview(adminKey, artistId);
        if (cancelled) return;
        setState({ kind: "ready", preview });
        setSelected(
          new Set(
            preview.candidates
              .filter((c) => c.status === "eligible")
              .slice(0, preview.would_add_count)
              .map((c) => c.similar_artist_name),
          ),
        );
      } catch (err) {
        if (cancelled) return;
        if (err instanceof AdminApiError && (err.status === 401 || err.status === 403)) {
          onAuthError();
          return;
        }
        setState({
          kind: "error",
          message:
            err instanceof Error ? err.message : "Failed to load hydration preview.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adminKey, artistId, onAuthError]);

  const eligibleCandidates = useMemo<AdminHydrationCandidate[]>(() => {
    if (state.kind !== "ready") return [];
    return state.preview.candidates.filter((c) => c.status === "eligible");
  }, [state]);

  const ineligibleCandidates = useMemo<AdminHydrationCandidate[]>(() => {
    if (state.kind !== "ready") return [];
    return state.preview.candidates.filter((c) => c.status !== "eligible");
  }, [state]);

  const toggleCandidate = (name: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const onConfirm = async (): Promise<void> => {
    if (state.kind !== "ready" || submitting) return;
    if (!adminEmail.trim()) {
      setSubmitError("Enter your email so the audit log can record this hydration.");
      return;
    }
    setSubmitError(null);
    setSubmitting(true);
    try {
      window.localStorage.setItem(ADMIN_EMAIL_STORAGE_KEY, adminEmail.trim());
    } catch {
      /* ignore */
    }
    try {
      const res = await executeHydration(adminKey, artistId, {
        adminEmail: adminEmail.trim(),
        confirmedCandidates: Array.from(selected),
      });
      setResult(res);
      onSuccess?.(res);
    } catch (err) {
      if (err instanceof AdminApiError && (err.status === 401 || err.status === 403)) {
        onAuthError();
        return;
      }
      setSubmitError(
        err instanceof Error ? err.message : "Failed to execute hydration.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const headerLabel = `Hydrate from ${artistName}`;
  const selectedCount = selected.size;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={headerLabel}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-2xl overflow-hidden rounded-lg bg-bg-white shadow-xl">
        <header className="border-b border-border px-6 py-4">
          <h2 className="text-lg font-semibold text-text-primary">{headerLabel}</h2>
          {state.kind === "ready" && !result && (
            <p className="mt-1 text-sm text-text-secondary">
              {state.preview.would_add_count > 0
                ? `Adding up to ${state.preview.would_add_count} similar artist${
                    state.preview.would_add_count === 1 ? "" : "s"
                  } from ${artistName}'s top matches.`
                : "No eligible additions for this artist."}
            </p>
          )}
        </header>

        <div className="max-h-[60vh] overflow-y-auto px-6 py-4">
          {state.kind === "loading" && (
            <p className="text-sm text-text-secondary">Loading preview…</p>
          )}
          {state.kind === "error" && (
            <p className="text-sm text-blush-accent">{state.message}</p>
          )}
          {state.kind === "ready" && result && (
            <SuccessSummary
              result={result}
              dailyCapRemaining={state.preview.daily_cap_remaining - result.added_count}
              source={artistName}
            />
          )}
          {state.kind === "ready" && !result && (
            <>
              {state.preview.blocking_reason && (
                <p
                  role="alert"
                  className="mb-3 rounded-md bg-blush-soft px-3 py-2 text-sm text-blush-accent"
                >
                  {state.preview.blocking_reason}
                </p>
              )}

              <ul className="space-y-2" data-testid="eligible-candidates">
                {eligibleCandidates.map((candidate) => (
                  <li
                    key={candidate.similar_artist_name}
                    className="flex items-start gap-3 rounded-md border border-border bg-bg-base px-3 py-2"
                  >
                    <input
                      type="checkbox"
                      id={`hydrate-${candidate.similar_artist_name}`}
                      checked={selected.has(candidate.similar_artist_name)}
                      onChange={() => toggleCandidate(candidate.similar_artist_name)}
                      className="mt-1"
                    />
                    <label
                      htmlFor={`hydrate-${candidate.similar_artist_name}`}
                      className="flex-1 cursor-pointer"
                    >
                      <span className="block text-sm font-medium text-text-primary">
                        {candidate.similar_artist_name}
                      </span>
                      <span className="block text-xs text-text-secondary">
                        {candidate.similarity_score.toFixed(2)} similarity
                      </span>
                    </label>
                  </li>
                ))}
              </ul>

              {ineligibleCandidates.length > 0 && (
                <details className="mt-4">
                  <summary className="cursor-pointer text-xs text-text-secondary">
                    Skipped ({ineligibleCandidates.length})
                  </summary>
                  <ul className="mt-2 space-y-1 text-xs text-text-secondary">
                    {ineligibleCandidates.map((candidate) => (
                      <li key={candidate.similar_artist_name}>
                        <span className="font-medium">
                          {candidate.similar_artist_name}
                        </span>
                        <span className="ml-2">
                          {candidate.status === "already_exists" &&
                            "already in database"}
                          {candidate.status === "below_threshold" &&
                            `below threshold (${candidate.similarity_score.toFixed(2)})`}
                          {candidate.status === "depth_exceeded" &&
                            "source artist at max hydration depth"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              <p className="mt-4 text-xs text-text-secondary">
                Daily cap: {state.preview.daily_cap_remaining} of 100 remaining.
              </p>

              <label className="mt-4 block text-sm text-text-primary">
                Your email (recorded in the audit log)
                <input
                  type="email"
                  value={adminEmail}
                  onChange={(e) => setAdminEmail(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
                  placeholder="ops@greenroom.test"
                />
              </label>

              {submitError && (
                <p className="mt-2 text-sm text-blush-accent">{submitError}</p>
              )}
            </>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border bg-bg-surface px-6 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-md border border-border bg-bg-white px-4 py-2 text-sm text-text-primary disabled:opacity-50"
          >
            {result ? "Close" : "Cancel"}
          </button>
          {state.kind === "ready" && !result && (
            <button
              type="button"
              onClick={() => void onConfirm()}
              disabled={
                submitting ||
                !state.preview.can_proceed ||
                selectedCount === 0
              }
              className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:opacity-50"
            >
              {submitting
                ? "Adding…"
                : `Add ${selectedCount} artist${selectedCount === 1 ? "" : "s"}`}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function SuccessSummary({
  result,
  dailyCapRemaining,
  source,
}: {
  result: AdminHydrationResult;
  dailyCapRemaining: number;
  source: string;
}): JSX.Element {
  if (result.added_count === 0) {
    return (
      <div>
        <p className="text-sm text-text-primary">
          No artists were added.
          {result.blocking_reason ? ` ${result.blocking_reason}` : ""}
        </p>
        <p className="mt-2 text-xs text-text-secondary">
          Daily cap: {Math.max(0, dailyCapRemaining)} of 100 remaining.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-sm font-medium text-text-primary">
        ✓ Added {result.added_count} artist
        {result.added_count === 1 ? "" : "s"}. Enrichment scheduled.
      </p>
      <p className="mt-1 text-xs text-text-secondary">
        {source}&apos;s catalog now includes the following hydrated artists:
      </p>
      <ul className="mt-3 space-y-1 text-sm">
        {result.added_artists.map((artist) => (
          <li key={artist.id} className="text-text-primary">
            {artist.name}
            <span className="ml-2 text-xs text-text-secondary">enriching…</span>
          </li>
        ))}
      </ul>
      {result.daily_cap_hit && (
        <p className="mt-3 text-xs text-blush-accent">
          Daily cap was hit — fewer artists were added than confirmed.
        </p>
      )}
      <p className="mt-3 text-xs text-text-secondary">
        Daily cap: {Math.max(0, dailyCapRemaining)} of 100 remaining.
      </p>
    </div>
  );
}
