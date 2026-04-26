/**
 * Tests for NotificationPreferencesForm.
 *
 * The form is the user's whole control surface for email — the toggles
 * for each per-type flag, the digest schedule, the frequency cap, and
 * the global pause/resume. Tests cover the patches that get sent for
 * each interaction plus the locking behaviour while paused (every
 * toggle disabled, but the resume button still works).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationPreferencesForm } from "@/components/settings/NotificationPreferencesForm";
import type { NotificationPreferences } from "@/types";

const updateNotificationPreferences = vi.fn();
const pauseAllEmails = vi.fn();
const resumeAllEmails = vi.fn();

vi.mock("@/lib/api/notification-preferences", () => ({
  updateNotificationPreferences: (token: string, patch: unknown) =>
    updateNotificationPreferences(token, patch),
  pauseAllEmails: (token: string) => pauseAllEmails(token),
  resumeAllEmails: (token: string) => resumeAllEmails(token),
}));

function prefs(overrides: Partial<NotificationPreferences> = {}): NotificationPreferences {
  return {
    artist_announcements: true,
    venue_announcements: true,
    selling_fast_alerts: true,
    show_reminders: true,
    show_reminder_days_before: 1,
    staff_picks: false,
    artist_spotlights: false,
    similar_artist_suggestions: false,
    weekly_digest: false,
    digest_day_of_week: "monday",
    digest_hour: 8,
    max_emails_per_week: 3,
    quiet_hours_start: 21,
    quiet_hours_end: 8,
    timezone: "America/New_York",
    paused: false,
    paused_at: null,
    ...overrides,
  };
}

describe("NotificationPreferencesForm", () => {
  beforeEach(() => {
    updateNotificationPreferences.mockReset();
    pauseAllEmails.mockReset();
    resumeAllEmails.mockReset();
    updateNotificationPreferences.mockImplementation(
      async (_t: string, patch: Partial<NotificationPreferences>) =>
        prefs(patch),
    );
    pauseAllEmails.mockResolvedValue(prefs({ paused: true, paused_at: "2026-04-26T00:00:00+00:00" }));
    resumeAllEmails.mockResolvedValue(prefs({ paused: false, paused_at: null }));
  });

  it("renders every section header", () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    expect(
      screen.getByRole("heading", { name: "Actionable alerts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Discovery" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Weekly digest" }),
    ).toBeInTheDocument();
  });

  it("toggling a per-type flag sends a single-field patch", async () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Staff picks/i }));
    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        staff_picks: true,
      });
    });
  });

  it("toggling weekly digest reveals the day and hour selectors", async () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    expect(
      screen.queryByLabelText("Digest day of week"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Weekly digest/i }));
    await waitFor(() => {
      expect(screen.getByLabelText("Digest day of week")).toBeInTheDocument();
      expect(screen.getByLabelText("Digest hour")).toBeInTheDocument();
    });
  });

  it("changing the reminder days select sends an int patch", async () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    const select = screen.getByLabelText(
      "Remind me how many days before",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "7" } });
    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        show_reminder_days_before: 7,
      });
    });
  });

  it("Unlimited cap sends max_emails_per_week=null", async () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    const select = screen.getByLabelText(
      "Maximum emails per week",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "unlimited" } });
    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        max_emails_per_week: null,
      });
    });
  });

  it("changing quiet hours start sends an int patch", async () => {
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    const select = screen.getByLabelText(
      "Quiet hours start",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "23" } });
    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        quiet_hours_start: 23,
      });
    });
  });

  it("clicking Pause all calls the pause endpoint and disables toggles", async () => {
    const { rerender } = render(
      <NotificationPreferencesForm token="jwt" initial={prefs()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pause all/i }));
    await waitFor(() => {
      expect(pauseAllEmails).toHaveBeenCalledWith("jwt");
    });

    // After the pause resolves, every toggle is disabled but the
    // resume button remains clickable.
    rerender(
      <NotificationPreferencesForm
        token="jwt"
        initial={prefs({ paused: true, paused_at: "2026-04-26T00:00:00+00:00" })}
      />,
    );
    const announceToggle = screen.getByRole("checkbox", {
      name: /Artist announcements/i,
    }) as HTMLInputElement;
    expect(announceToggle.disabled).toBe(true);
    expect(
      screen.getByRole("button", { name: /Resume emails/i }),
    ).toBeEnabled();
  });

  it("clicking Resume calls the resume endpoint when paused", async () => {
    render(
      <NotificationPreferencesForm
        token="jwt"
        initial={prefs({ paused: true, paused_at: "2026-04-26T00:00:00+00:00" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Resume emails/i }));
    await waitFor(() => {
      expect(resumeAllEmails).toHaveBeenCalledWith("jwt");
      expect(pauseAllEmails).not.toHaveBeenCalled();
    });
  });

  it("shows an error banner when the API rejects the patch", async () => {
    updateNotificationPreferences.mockRejectedValueOnce(
      new Error("digest_hour must be between 0 and 23."),
    );
    render(<NotificationPreferencesForm token="jwt" initial={prefs()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Staff picks/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
