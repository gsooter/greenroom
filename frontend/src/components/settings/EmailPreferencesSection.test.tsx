/**
 * Tests for EmailPreferencesSection.
 *
 * Covers: loading the prefs row on mount, rendering only the weekly
 * digest feature today, optimistic toggle behaviour with revert on
 * failure, and showing day/hour controls when the digest is on.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EmailPreferencesSection } from "@/components/settings/EmailPreferencesSection";
import type { NotificationPreferences } from "@/types";

const getNotificationPreferences = vi.fn();
const updateNotificationPreferences = vi.fn();

vi.mock("@/lib/api/notification-preferences", () => ({
  getNotificationPreferences: (token: string) =>
    getNotificationPreferences(token),
  updateNotificationPreferences: (
    token: string,
    patch: Record<string, unknown>,
  ) => updateNotificationPreferences(token, patch),
  pauseAllEmails: vi.fn(),
  resumeAllEmails: vi.fn(),
}));

function makePrefs(
  overrides: Partial<NotificationPreferences> = {},
): NotificationPreferences {
  return {
    artist_announcements: false,
    venue_announcements: false,
    selling_fast_alerts: false,
    show_reminders: false,
    show_reminder_days_before: 1,
    staff_picks: false,
    artist_spotlights: false,
    similar_artist_suggestions: false,
    weekly_digest: false,
    digest_day_of_week: "monday",
    digest_hour: 8,
    max_emails_per_week: null,
    quiet_hours_start: 22,
    quiet_hours_end: 7,
    timezone: "America/New_York",
    paused: false,
    paused_at: null,
    ...overrides,
  };
}

describe("EmailPreferencesSection", () => {
  beforeEach(() => {
    getNotificationPreferences.mockReset();
    updateNotificationPreferences.mockReset();
  });

  it("renders only the weekly digest feature today", async () => {
    getNotificationPreferences.mockResolvedValueOnce(makePrefs());

    render(<EmailPreferencesSection token="jwt" />);

    expect(
      await screen.findByRole("checkbox", { name: /weekly digest/i }),
    ).toBeInTheDocument();
    // No other shipped email features for now.
    expect(
      screen.queryByRole("checkbox", { name: /show reminders/i }),
    ).not.toBeInTheDocument();
    // Forward-looking caption is present.
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument();
  });

  it("flips weekly_digest optimistically and reveals day/hour controls", async () => {
    getNotificationPreferences.mockResolvedValueOnce(makePrefs());
    updateNotificationPreferences.mockResolvedValueOnce(
      makePrefs({ weekly_digest: true }),
    );

    render(<EmailPreferencesSection token="jwt" />);

    const toggle = await screen.findByRole("checkbox", {
      name: /weekly digest/i,
    });
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        weekly_digest: true,
      });
    });

    // Day/hour controls only appear when the digest is on.
    expect(
      await screen.findByLabelText("Digest day of week"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Digest hour")).toBeInTheDocument();
  });

  it("reverts the toggle and surfaces an error when the API fails", async () => {
    getNotificationPreferences.mockResolvedValueOnce(makePrefs());
    updateNotificationPreferences.mockRejectedValueOnce(new Error("boom"));

    render(<EmailPreferencesSection token="jwt" />);

    const toggle = await screen.findByRole("checkbox", {
      name: /weekly digest/i,
    });
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByText(/could not save that change/i)).toBeInTheDocument();
    });
    expect(
      (
        screen.getByRole("checkbox", {
          name: /weekly digest/i,
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
  });

  it("PATCHes the digest day when the day select changes", async () => {
    getNotificationPreferences.mockResolvedValueOnce(
      makePrefs({ weekly_digest: true }),
    );
    updateNotificationPreferences.mockResolvedValueOnce(
      makePrefs({ weekly_digest: true, digest_day_of_week: "friday" }),
    );

    render(<EmailPreferencesSection token="jwt" />);

    const daySelect = await screen.findByLabelText("Digest day of week");
    fireEvent.change(daySelect, { target: { value: "friday" } });

    await waitFor(() => {
      expect(updateNotificationPreferences).toHaveBeenCalledWith("jwt", {
        digest_day_of_week: "friday",
      });
    });
  });
});
