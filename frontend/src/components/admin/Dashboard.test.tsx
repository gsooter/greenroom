/**
 * Tests for the admin Dashboard component.
 *
 * Covers: renders all four sections, hydration leaderboard's
 * "Hydrate" button mounts the modal.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "@/components/admin/Dashboard";
import * as adminApi from "@/lib/api/admin";

vi.mock("@/lib/api/admin", async () => {
  const actual = await vi.importActual<typeof adminApi>("@/lib/api/admin");
  return {
    ...actual,
    getAdminDashboard: vi.fn(),
    getHydrationPreview: vi.fn(),
    executeHydration: vi.fn(),
    triggerMassHydration: vi.fn(),
  };
});

const mocked = adminApi as unknown as {
  getAdminDashboard: ReturnType<typeof vi.fn>;
  getHydrationPreview: ReturnType<typeof vi.fn>;
  executeHydration: ReturnType<typeof vi.fn>;
  triggerMassHydration: ReturnType<typeof vi.fn>;
};

const SNAPSHOT: adminApi.AdminDashboardSnapshot = {
  users: { total: 42, breakdown: { active_last_30d: 5, signed_in_inactive: 30, deactivated: 7 } },
  artists: { total: 110, breakdown: { original: 100, hydrated: 10 } },
  events: { total: 200, breakdown: { upcoming: 50, past: 145, cancelled: 5 } },
  venues: { total: 12, breakdown: { active: 10, inactive: 2 } },
  music_connections: { spotify: 4, apple_music: 1, tidal: 0 },
  push_subscriptions: { active: 6, disabled: 1 },
  email_enabled_users: 18,
  activity: [
    {
      label: "24 hours",
      new_users: 1,
      new_events: 7,
      push_sends: 3,
      email_sends: 0,
      hydrations_run: 0,
      hydration_artists_added: 0,
    },
    {
      label: "7 days",
      new_users: 4,
      new_events: 22,
      push_sends: 9,
      email_sends: 5,
      hydrations_run: 2,
      hydration_artists_added: 7,
    },
    {
      label: "30 days",
      new_users: 18,
      new_events: 80,
      push_sends: 50,
      email_sends: 12,
      hydrations_run: 4,
      hydration_artists_added: 14,
    },
  ],
  health: [
    {
      label: "Last successful scrape",
      value: "2026-05-03T12:00:00+00:00",
      status: "green",
      detail: "10 venues scraped in last 24h",
    },
    {
      label: "Push delivery (24h)",
      value: "98.5%",
      status: "green",
      detail: null,
    },
    {
      label: "Email bounce rate (7d)",
      value: "1.2%",
      status: "green",
      detail: null,
    },
    {
      label: "Recommendation cache hit rate",
      value: "unknown",
      status: "yellow",
      detail: null,
    },
  ],
  most_hydrated: [
    { artist_id: "00000000-0000-0000-0000-000000000010", artist_name: "Caamp", hydration_count: 5 },
  ],
  best_candidates: [
    {
      artist_id: "00000000-0000-0000-0000-000000000020",
      artist_name: "Phoebe Bridgers",
      candidate_count: 7,
      top_candidate_name: "Lucy Dacus",
    },
  ],
  daily_hydration_remaining: 87,
};

describe("Dashboard", () => {
  beforeEach(() => {
    mocked.getAdminDashboard.mockReset();
    mocked.getHydrationPreview.mockReset();
    mocked.executeHydration.mockReset();
    mocked.triggerMassHydration.mockReset();
    window.localStorage.clear();
  });

  it("renders all four sections from the snapshot", async () => {
    mocked.getAdminDashboard.mockResolvedValue(SNAPSHOT);

    render(<Dashboard adminKey="key" signOut={() => {}} />);

    expect(
      await screen.findByRole("heading", { name: /system counts/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /recent activity/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^health$/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /hydration leaderboard/i }),
    ).toBeInTheDocument();

    // Counts surfaced
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("110")).toBeInTheDocument();
    expect(screen.getByText("Caamp")).toBeInTheDocument();
    expect(screen.getByText(/Phoebe Bridgers/)).toBeInTheDocument();
    expect(screen.getByText(/top: lucy dacus/i)).toBeInTheDocument();
    // Daily cap card
    expect(screen.getByText(/daily hydration cap/i)).toBeInTheDocument();
  });

  it("opens the hydration modal when a leaderboard Hydrate button is clicked", async () => {
    mocked.getAdminDashboard.mockResolvedValue(SNAPSHOT);
    mocked.getHydrationPreview.mockResolvedValue({
      source_artist: {
        id: "00000000-0000-0000-0000-000000000020",
        name: "Phoebe Bridgers",
        normalized_name: "phoebe bridgers",
        hydration_source: null,
        hydration_depth: 0,
        hydrated_from_artist_id: null,
        hydrated_at: null,
      },
      candidates: [
        {
          similar_artist_name: "Lucy Dacus",
          similar_artist_mbid: null,
          similarity_score: 0.92,
          status: "eligible",
          existing_artist_id: null,
        },
      ],
      eligible_count: 1,
      would_add_count: 1,
      daily_cap_remaining: 87,
      can_proceed: true,
      blocking_reason: null,
    });

    render(<Dashboard adminKey="key" signOut={() => {}} />);

    fireEvent.click(
      await screen.findByRole("button", { name: /^hydrate$/i }),
    );

    await waitFor(() => {
      expect(mocked.getHydrationPreview).toHaveBeenCalledWith(
        "key",
        "00000000-0000-0000-0000-000000000020",
      );
    });
    expect(
      await screen.findByRole("dialog", { name: /hydrate from phoebe bridgers/i }),
    ).toBeInTheDocument();
  });

  it("triggers mass hydration after prompt + confirm", async () => {
    mocked.getAdminDashboard.mockResolvedValue(SNAPSHOT);
    mocked.triggerMassHydration.mockResolvedValue({
      task_id: "task-123",
      status: "queued",
      admin_email: "ops@greenroom.test",
    });
    const promptSpy = vi
      .spyOn(window, "prompt")
      .mockReturnValue("ops@greenroom.test");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<Dashboard adminKey="key" signOut={() => {}} />);

    fireEvent.click(
      await screen.findByRole("button", { name: /mass hydrate now/i }),
    );

    await waitFor(() => {
      expect(mocked.triggerMassHydration).toHaveBeenCalledWith(
        "key",
        "ops@greenroom.test",
      );
    });
    expect(
      await screen.findByText(/mass hydration queued.*task-123/i),
    ).toBeInTheDocument();
    promptSpy.mockRestore();
    confirmSpy.mockRestore();
  });

  it("disables mass-hydrate button when daily cap is exhausted", async () => {
    mocked.getAdminDashboard.mockResolvedValue({
      ...SNAPSHOT,
      daily_hydration_remaining: 0,
    });

    render(<Dashboard adminKey="key" signOut={() => {}} />);

    const button = await screen.findByRole("button", {
      name: /daily cap reached/i,
    });
    expect(button).toBeDisabled();
  });
});
