/**
 * Inline email-preferences card for the /settings page.
 *
 * Renders one row per email type the dispatcher can send so users can
 * opt in or out of each channel without leaving the main settings
 * page. Each row maps 1:1 to a boolean column on the
 * ``NotificationPreferences`` row, so the unsubscribe links in real
 * outbound mail flip the same flag the user toggles here.
 *
 * The richer ``/settings/notifications`` page still exists for the
 * advanced controls — quiet hours, frequency caps, pause-all,
 * "send a test" buttons — and a footer link points there.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
} from "@/lib/api/notification-preferences";
import type {
  DigestDayOfWeek,
  NotificationPreferences,
  NotificationPreferencesPatch,
  ReminderDays,
} from "@/types";

interface Props {
  token: string | null;
}

const DIGEST_DAYS: { value: DigestDayOfWeek; label: string }[] = [
  { value: "monday", label: "Monday" },
  { value: "tuesday", label: "Tuesday" },
  { value: "wednesday", label: "Wednesday" },
  { value: "thursday", label: "Thursday" },
  { value: "friday", label: "Friday" },
  { value: "saturday", label: "Saturday" },
  { value: "sunday", label: "Sunday" },
];

interface EmailFeature {
  /** Stable key so React reconciles the row across renders. */
  key: string;
  label: string;
  description: string;
  /** Read the on/off state of this feature from the prefs row. */
  enabled: (prefs: NotificationPreferences) => boolean;
  /**
   * Build the patch sent to ``PATCH /me/notification-preferences`` when
   * the toggle flips.
   */
  togglePatch: (next: boolean) => NotificationPreferencesPatch;
  /**
   * Optional follow-on controls (day pickers, etc.) rendered below the
   * toggle when the feature is on.
   */
  detail?: (
    prefs: NotificationPreferences,
    apply: (patch: NotificationPreferencesPatch) => Promise<void>,
  ) => JSX.Element;
}

const REMINDER_DAY_OPTIONS: ReminderDays[] = [1, 2, 7];

const FEATURES: EmailFeature[] = [
  {
    key: "artist_announcements",
    label: "Artist announcements",
    description:
      "Email me when an artist I follow announces a DC show.",
    enabled: (p) => p.artist_announcements,
    togglePatch: (next) => ({ artist_announcements: next }),
  },
  {
    key: "venue_announcements",
    label: "Venue announcements",
    description: "Email me when a venue I follow adds a new show.",
    enabled: (p) => p.venue_announcements,
    togglePatch: (next) => ({ venue_announcements: next }),
  },
  {
    key: "selling_fast_alerts",
    label: "Selling fast alerts",
    description:
      "Email me when a saved show crosses our 'selling fast' threshold.",
    enabled: (p) => p.selling_fast_alerts,
    togglePatch: (next) => ({ selling_fast_alerts: next }),
  },
  {
    key: "show_reminders",
    label: "Show reminders",
    description: "Email me before a show I've saved or RSVP'd to.",
    enabled: (p) => p.show_reminders,
    togglePatch: (next) => ({ show_reminders: next }),
    detail: (prefs, apply) => (
      <SelectField
        label="Remind me"
        ariaLabel="How many days before"
        value={String(prefs.show_reminder_days_before)}
        onChange={(v) =>
          void apply({
            show_reminder_days_before: Number(v) as ReminderDays,
          })
        }
        options={REMINDER_DAY_OPTIONS.map((d) => ({
          value: String(d),
          label: d === 1 ? "1 day before" : `${d} days before`,
        }))}
      />
    ),
  },
  {
    key: "staff_picks",
    label: "Staff picks",
    description: "Hand-curated shows our editorial team is excited about.",
    enabled: (p) => p.staff_picks,
    togglePatch: (next) => ({ staff_picks: next }),
  },
  {
    key: "artist_spotlights",
    label: "Artist spotlights",
    description: "Monthly deep-dives on artists touring through DC.",
    enabled: (p) => p.artist_spotlights,
    togglePatch: (next) => ({ artist_spotlights: next }),
  },
  {
    key: "similar_artist_suggestions",
    label: "Similar artist suggestions",
    description: "Artists similar to the ones I already follow.",
    enabled: (p) => p.similar_artist_suggestions,
    togglePatch: (next) => ({ similar_artist_suggestions: next }),
  },
  {
    key: "weekly_digest",
    label: "Weekly digest",
    description:
      "A once-a-week rundown of upcoming shows tailored to your follows and taste.",
    enabled: (p) => p.weekly_digest,
    togglePatch: (next) => ({ weekly_digest: next }),
    detail: (prefs, apply) => (
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label="Send on"
          ariaLabel="Digest day of week"
          value={prefs.digest_day_of_week}
          onChange={(v) =>
            void apply({ digest_day_of_week: v as DigestDayOfWeek })
          }
          options={DIGEST_DAYS.map((d) => ({ value: d.value, label: d.label }))}
        />
        <SelectField
          label="At"
          ariaLabel="Digest hour"
          value={String(prefs.digest_hour)}
          onChange={(v) => void apply({ digest_hour: Number(v) })}
          options={Array.from({ length: 24 }, (_, h) => ({
            value: String(h),
            label: formatHour(h),
          }))}
        />
      </div>
    ),
  },
];

/**
 * Render the email preferences card on the settings page.
 *
 * Loads the user's notification prefs once on mount and PATCHes the
 * whole row on every toggle. Saves are optimistic — the UI updates
 * immediately and reverts if the API call fails.
 *
 * Args:
 *     token: Current session token. ``null`` short-circuits the
 *         loading state until auth resolves.
 *
 * Returns:
 *     A ``<section>`` with one row per email feature.
 */
export function EmailPreferencesSection({ token }: Props): JSX.Element {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

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
            : "Could not load your email preferences.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const apply = useCallback(
    async (patch: NotificationPreferencesPatch): Promise<void> => {
      if (!token || !prefs) return;
      const previous = prefs;
      setPrefs({ ...prefs, ...patch } as NotificationPreferences);
      setSaveError(null);
      try {
        const next = await updateNotificationPreferences(token, patch);
        setPrefs(next);
      } catch (err) {
        setPrefs(previous);
        setSaveError(
          err instanceof ApiRequestError
            ? err.message
            : "Could not save that change. Try again.",
        );
      }
    },
    [token, prefs],
  );

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">
        Email preferences
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        Tune which emails Greenroom sends you. Changes save as you toggle.
      </p>

      {loadError ? (
        <p className="mt-4 text-xs text-blush-accent" role="alert">
          {loadError}
        </p>
      ) : prefs === null ? (
        <p className="mt-4 text-xs text-text-secondary">Loading…</p>
      ) : (
        <ul className="mt-4 divide-y divide-border rounded-lg border border-border bg-bg-white">
          {FEATURES.map((feature) => (
            <EmailFeatureRow
              key={feature.key}
              feature={feature}
              prefs={prefs}
              apply={apply}
            />
          ))}
        </ul>
      )}

      {saveError ? (
        <p className="mt-3 text-xs text-blush-accent" role="alert">
          {saveError}
        </p>
      ) : null}

      <p className="mt-3 text-[11px] text-text-secondary/80">
        Want quiet hours, weekly caps, or to send yourself a test email?{" "}
        <Link
          href="/settings/notifications"
          className="underline underline-offset-2"
        >
          Open the full notification settings.
        </Link>
      </p>
    </section>
  );
}

function EmailFeatureRow({
  feature,
  prefs,
  apply,
}: {
  feature: EmailFeature;
  prefs: NotificationPreferences;
  apply: (patch: NotificationPreferencesPatch) => Promise<void>;
}): JSX.Element {
  const enabled = feature.enabled(prefs);

  return (
    <li className="p-4">
      <label className="flex cursor-pointer items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-text-primary">
            {feature.label}
          </p>
          <p className="mt-0.5 text-xs text-text-secondary">
            {feature.description}
          </p>
        </div>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => void apply(feature.togglePatch(e.target.checked))}
          aria-label={feature.label}
          className="mt-1 h-4 w-4 shrink-0"
        />
      </label>
      {enabled && feature.detail ? (
        <div className="mt-4 border-t border-border/60 pt-4">
          {feature.detail(prefs, apply)}
        </div>
      ) : null}
    </li>
  );
}

function SelectField({
  label,
  ariaLabel,
  value,
  onChange,
  options,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (next: string) => void;
  options: { value: string; label: string }[];
}): JSX.Element {
  return (
    <label className="block">
      <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
        {label}
      </span>
      <select
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function formatHour(h: number): string {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}
