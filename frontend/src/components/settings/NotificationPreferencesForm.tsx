/**
 * Settings UI for /me/notification-preferences.
 *
 * Rendered inside the /settings/notifications page. Owns the local
 * draft state, debounced save status, and the global pause/resume
 * toggle. Logic lives here so the component can be tested in isolation
 * without spinning up Next.js routing.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import {
  pauseAllEmails,
  resumeAllEmails,
  updateNotificationPreferences,
} from "@/lib/api/notification-preferences";
import type {
  DigestDayOfWeek,
  MaxEmailsPerWeek,
  NotificationPreferences,
  NotificationPreferencesPatch,
  ReminderDays,
} from "@/types";

const DAYS: { value: DigestDayOfWeek; label: string }[] = [
  { value: "monday", label: "Monday" },
  { value: "tuesday", label: "Tuesday" },
  { value: "wednesday", label: "Wednesday" },
  { value: "thursday", label: "Thursday" },
  { value: "friday", label: "Friday" },
  { value: "saturday", label: "Saturday" },
  { value: "sunday", label: "Sunday" },
];

const REMINDER_DAYS: ReminderDays[] = [1, 2, 7];

const MAX_PER_WEEK_OPTIONS: { value: MaxEmailsPerWeek | "unlimited"; label: string }[] =
  [
    { value: 1, label: "1 / week" },
    { value: 3, label: "3 / week" },
    { value: 7, label: "7 / week" },
    { value: "unlimited", label: "Unlimited" },
  ];

export interface NotificationPreferencesFormProps {
  token: string;
  initial: NotificationPreferences;
}

export function NotificationPreferencesForm({
  token,
  initial,
}: NotificationPreferencesFormProps): JSX.Element {
  const [prefs, setPrefs] = useState<NotificationPreferences>(initial);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPrefs(initial);
  }, [initial]);

  const applyPatch = useCallback(
    async (patch: NotificationPreferencesPatch): Promise<void> => {
      setStatus("saving");
      setError(null);
      try {
        const next = await updateNotificationPreferences(token, patch);
        setPrefs(next);
        setStatus("saved");
      } catch (err) {
        setStatus("error");
        setError(
          err instanceof ApiRequestError
            ? err.message
            : "Could not save changes.",
        );
      }
    },
    [token],
  );

  const handlePause = useCallback(async (): Promise<void> => {
    setStatus("saving");
    setError(null);
    try {
      const next = prefs.paused
        ? await resumeAllEmails(token)
        : await pauseAllEmails(token);
      setPrefs(next);
      setStatus("saved");
    } catch (err) {
      setStatus("error");
      setError(
        err instanceof ApiRequestError ? err.message : "Could not save changes.",
      );
    }
  }, [token, prefs.paused]);

  return (
    <div className="space-y-8">
      <PauseBanner paused={prefs.paused} onToggle={() => void handlePause()} />

      <Section
        title="Actionable alerts"
        description="Direct notifications about shows, artists, and venues you're tracking."
      >
        <Toggle
          label="Artist announcements"
          description="A followed artist's show is added to the calendar."
          checked={prefs.artist_announcements}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ artist_announcements: v })}
        />
        <Toggle
          label="Venue announcements"
          description="A followed venue adds a new show."
          checked={prefs.venue_announcements}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ venue_announcements: v })}
        />
        <Toggle
          label="Selling fast alerts"
          description="A saved show crosses our 'selling fast' threshold."
          checked={prefs.selling_fast_alerts}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ selling_fast_alerts: v })}
        />
        <Toggle
          label="Show reminders"
          description="A reminder before a show you've saved or RSVP'd to."
          checked={prefs.show_reminders}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ show_reminders: v })}
        />
        {prefs.show_reminders ? (
          <Field label="Remind me">
            <select
              aria-label="Remind me how many days before"
              value={prefs.show_reminder_days_before}
              disabled={prefs.paused}
              onChange={(e) =>
                void applyPatch({
                  show_reminder_days_before: Number(e.target.value) as ReminderDays,
                })
              }
              className="rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
            >
              {REMINDER_DAYS.map((d) => (
                <option key={d} value={d}>
                  {d === 1 ? "1 day before" : `${d} days before`}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
      </Section>

      <Section
        title="Discovery"
        description="Less-frequent emails to surface things you might not have heard."
      >
        <Toggle
          label="Staff picks"
          description="Hand-curated shows from the editorial team."
          checked={prefs.staff_picks}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ staff_picks: v })}
        />
        <Toggle
          label="Artist spotlights"
          description="Monthly deep-dives on artists touring through your city."
          checked={prefs.artist_spotlights}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ artist_spotlights: v })}
        />
        <Toggle
          label="Similar artist suggestions"
          description="Artists similar to ones you already follow."
          checked={prefs.similar_artist_suggestions}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ similar_artist_suggestions: v })}
        />
      </Section>

      <Section
        title="Weekly digest"
        description="Once-a-week recap of upcoming shows tailored to you."
      >
        <Toggle
          label="Weekly digest"
          description="Off by default. Turn on for a Monday-morning rundown."
          checked={prefs.weekly_digest}
          disabled={prefs.paused}
          onChange={(v) => void applyPatch({ weekly_digest: v })}
        />
        {prefs.weekly_digest ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Day">
              <select
                aria-label="Digest day of week"
                value={prefs.digest_day_of_week}
                disabled={prefs.paused}
                onChange={(e) =>
                  void applyPatch({
                    digest_day_of_week: e.target.value as DigestDayOfWeek,
                  })
                }
                className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
              >
                {DAYS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Hour">
              <select
                aria-label="Digest hour"
                value={prefs.digest_hour}
                disabled={prefs.paused}
                onChange={(e) =>
                  void applyPatch({ digest_hour: Number(e.target.value) })
                }
                className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {formatHour(h)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        ) : null}
      </Section>

      <Section
        title="Frequency &amp; quiet hours"
        description="Caps and timing applied across every email type."
      >
        <Field label="Maximum emails per week">
          <select
            aria-label="Maximum emails per week"
            value={prefs.max_emails_per_week ?? "unlimited"}
            disabled={prefs.paused}
            onChange={(e) => {
              const raw = e.target.value;
              void applyPatch({
                max_emails_per_week:
                  raw === "unlimited" ? null : (Number(raw) as MaxEmailsPerWeek),
              });
            }}
            className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
          >
            {MAX_PER_WEEK_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Quiet hours start">
            <select
              aria-label="Quiet hours start"
              value={prefs.quiet_hours_start}
              disabled={prefs.paused}
              onChange={(e) =>
                void applyPatch({ quiet_hours_start: Number(e.target.value) })
              }
              className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {formatHour(h)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Quiet hours end">
            <select
              aria-label="Quiet hours end"
              value={prefs.quiet_hours_end}
              disabled={prefs.paused}
              onChange={(e) =>
                void applyPatch({ quiet_hours_end: Number(e.target.value) })
              }
              className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {formatHour(h)}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      <div className="min-h-[1rem] text-xs">
        {status === "saving" ? (
          <span className="text-text-secondary">Saving…</span>
        ) : null}
        {status === "saved" ? (
          <span className="text-text-secondary" role="status">
            Saved.
          </span>
        ) : null}
        {status === "error" && error ? (
          <span className="text-blush-accent" role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function PauseBanner({
  paused,
  onToggle,
}: {
  paused: boolean;
  onToggle: () => void;
}): JSX.Element {
  return (
    <div
      className={
        "rounded-lg border p-4 " +
        (paused
          ? "border-blush-accent bg-blush-soft"
          : "border-border bg-bg-white")
      }
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-text-primary">
            {paused ? "All emails paused" : "Pause all emails"}
          </p>
          <p className="mt-1 text-xs text-text-secondary">
            {paused
              ? "We'll keep your per-type settings. Resume to start receiving them again."
              : "Stops every email without erasing your toggles. You can resume anytime."}
          </p>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="rounded-md border border-green-primary px-3 py-1.5 text-xs font-medium text-green-primary transition hover:bg-green-primary hover:text-text-inverse"
        >
          {paused ? "Resume emails" : "Pause all"}
        </button>
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold text-text-primary">{title}</h2>
        <p className="mt-0.5 text-xs text-text-secondary">{description}</p>
      </div>
      <div className="divide-y divide-border rounded-lg border border-border bg-bg-white">
        {children}
      </div>
    </section>
  );
}

function Toggle({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}): JSX.Element {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary">{label}</p>
        <p className="mt-0.5 text-xs text-text-secondary">{description}</p>
      </div>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label={label}
        className="mt-1 h-4 w-4 shrink-0"
      />
    </label>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <label className="block p-4">
      <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
        {label}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function formatHour(h: number): string {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}
