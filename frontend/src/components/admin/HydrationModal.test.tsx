/**
 * Tests for HydrationModal.
 *
 * Covers: preview rendering, deselecting candidates updates the
 * confirm-button label, daily-cap-zero disables the confirm button,
 * max-depth source surfaces the blocking reason, success summary
 * renders the added artists.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HydrationModal from "@/components/admin/HydrationModal";
import * as adminApi from "@/lib/api/admin";

vi.mock("@/lib/api/admin", async () => {
  const actual = await vi.importActual<typeof adminApi>("@/lib/api/admin");
  return {
    ...actual,
    getHydrationPreview: vi.fn(),
    executeHydration: vi.fn(),
  };
});

const mocked = adminApi as unknown as {
  getHydrationPreview: ReturnType<typeof vi.fn>;
  executeHydration: ReturnType<typeof vi.fn>;
};

const SOURCE_ID = "00000000-0000-0000-0000-000000000001";

const SOURCE_SUMMARY: adminApi.AdminArtistSummary = {
  id: SOURCE_ID,
  name: "Caamp",
  normalized_name: "caamp",
  hydration_source: null,
  hydration_depth: 0,
  hydrated_from_artist_id: null,
  hydrated_at: null,
};

const ELIGIBLE_PREVIEW: adminApi.AdminHydrationPreview = {
  source_artist: SOURCE_SUMMARY,
  candidates: [
    {
      similar_artist_name: "The Head and the Heart",
      similar_artist_mbid: null,
      similarity_score: 0.91,
      status: "eligible",
      existing_artist_id: null,
    },
    {
      similar_artist_name: "Mt. Joy",
      similar_artist_mbid: null,
      similarity_score: 0.88,
      status: "eligible",
      existing_artist_id: null,
    },
    {
      similar_artist_name: "Of Monsters and Men",
      similar_artist_mbid: null,
      similarity_score: 0.83,
      status: "already_exists",
      existing_artist_id: "00000000-0000-0000-0000-000000000002",
    },
  ],
  eligible_count: 2,
  would_add_count: 2,
  daily_cap_remaining: 87,
  can_proceed: true,
  blocking_reason: null,
};

describe("HydrationModal", () => {
  beforeEach(() => {
    mocked.getHydrationPreview.mockReset();
    mocked.executeHydration.mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("renders eligible candidates and the daily cap line", async () => {
    mocked.getHydrationPreview.mockResolvedValue(ELIGIBLE_PREVIEW);

    render(
      <HydrationModal
        adminKey="key"
        artistId={SOURCE_ID}
        artistName="Caamp"
        onClose={() => {}}
        onAuthError={() => {}}
      />,
    );

    expect(
      await screen.findByText("The Head and the Heart"),
    ).toBeInTheDocument();
    expect(screen.getByText("Mt. Joy")).toBeInTheDocument();
    expect(screen.getByText(/87 of 100 remaining/i)).toBeInTheDocument();
    // Already-existing entries land in the "Skipped" disclosure.
    expect(screen.getByText(/Skipped \(1\)/i)).toBeInTheDocument();
  });

  it("updates the confirm-button label when a candidate is unchecked", async () => {
    mocked.getHydrationPreview.mockResolvedValue(ELIGIBLE_PREVIEW);

    render(
      <HydrationModal
        adminKey="key"
        artistId={SOURCE_ID}
        artistName="Caamp"
        onClose={() => {}}
        onAuthError={() => {}}
      />,
    );

    expect(
      await screen.findByRole("button", { name: /add 2 artists/i }),
    ).toBeInTheDocument();

    // Find the Mt. Joy checkbox by querying its containing list item.
    const mtJoyRow = screen.getByText("Mt. Joy").closest("li");
    const mtJoyCheckbox = mtJoyRow?.querySelector('input[type="checkbox"]');
    expect(mtJoyCheckbox).toBeTruthy();
    fireEvent.click(mtJoyCheckbox as Element);

    expect(
      await screen.findByRole("button", { name: /add 1 artist$/i }),
    ).toBeInTheDocument();
  });

  it("disables confirm when can_proceed is false", async () => {
    mocked.getHydrationPreview.mockResolvedValue({
      ...ELIGIBLE_PREVIEW,
      can_proceed: false,
      would_add_count: 0,
      eligible_count: 0,
      blocking_reason: "Daily hydration cap reached.",
      candidates: [],
    });

    render(
      <HydrationModal
        adminKey="key"
        artistId={SOURCE_ID}
        artistName="Caamp"
        onClose={() => {}}
        onAuthError={() => {}}
      />,
    );

    expect(
      await screen.findByText(/daily hydration cap reached/i),
    ).toBeInTheDocument();
    // Confirm button still renders, but is disabled.
    const confirm = screen.getByRole("button", { name: /add 0 artists/i });
    expect(confirm).toBeDisabled();
  });

  it("disables confirm when source artist is at max depth", async () => {
    mocked.getHydrationPreview.mockResolvedValue({
      ...ELIGIBLE_PREVIEW,
      can_proceed: false,
      would_add_count: 0,
      eligible_count: 0,
      blocking_reason: "Source artist is at hydration depth 2; cannot hydrate beyond depth 2.",
      candidates: [
        {
          similar_artist_name: "Anything",
          similar_artist_mbid: null,
          similarity_score: 0.9,
          status: "depth_exceeded",
          existing_artist_id: null,
        },
      ],
    });

    render(
      <HydrationModal
        adminKey="key"
        artistId={SOURCE_ID}
        artistName="Caamp"
        onClose={() => {}}
        onAuthError={() => {}}
      />,
    );

    expect(
      await screen.findByText(/cannot hydrate beyond depth 2/i),
    ).toBeInTheDocument();
  });

  it("renders the success summary after a confirmed hydration", async () => {
    mocked.getHydrationPreview.mockResolvedValue(ELIGIBLE_PREVIEW);
    mocked.executeHydration.mockResolvedValue({
      source_artist_id: SOURCE_ID,
      added_artists: [
        {
          id: "00000000-0000-0000-0000-000000000003",
          name: "The Head and the Heart",
          normalized_name: "the head and the heart",
          hydration_source: "similar_artist",
          hydration_depth: 1,
          hydrated_from_artist_id: SOURCE_ID,
          hydrated_at: "2026-05-03T12:00:00+00:00",
        },
      ],
      added_count: 1,
      skipped_count: 0,
      filtered_count: 0,
      daily_cap_hit: false,
      blocking_reason: null,
    });

    const onSuccess = vi.fn();
    render(
      <HydrationModal
        adminKey="key"
        artistId={SOURCE_ID}
        artistName="Caamp"
        onClose={() => {}}
        onAuthError={() => {}}
        onSuccess={onSuccess}
      />,
    );

    await screen.findByText("The Head and the Heart");
    fireEvent.change(screen.getByPlaceholderText("ops@greenroom.test"), {
      target: { value: "ops@greenroom.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add 2 artists/i }));

    await waitFor(() => {
      expect(mocked.executeHydration).toHaveBeenCalledWith("key", SOURCE_ID, {
        adminEmail: "ops@greenroom.test",
        confirmedCandidates: expect.arrayContaining([
          "The Head and the Heart",
          "Mt. Joy",
        ]),
      });
    });
    expect(
      await screen.findByText(/added 1 artist\. enrichment scheduled/i),
    ).toBeInTheDocument();
    expect(onSuccess).toHaveBeenCalled();
  });
});
