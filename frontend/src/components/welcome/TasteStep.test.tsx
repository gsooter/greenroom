/**
 * Tests for TasteStep.
 *
 * The step juggles three bits of server state (genre catalog, current
 * genre_preferences, follow graph) and two async writes (PATCH /me,
 * follow/unfollow artist). Tests lock the behaviours a route change
 * could silently break: genre toggling updates selection locally,
 * Continue saves genre_preferences before advancing, and Skip always
 * advances even with nothing selected.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TasteStep } from "@/components/welcome/TasteStep";
import type { User } from "@/types";

const listGenres = vi.fn();
const updateMe = vi.fn();
const searchArtists = vi.fn();
const followArtist = vi.fn();
const unfollowArtist = vi.fn();

vi.mock("@/lib/api/onboarding", () => ({
  listGenres: () => listGenres(),
}));

vi.mock("@/lib/api/me", () => ({
  updateMe: (token: string, patch: unknown) => updateMe(token, patch),
}));

vi.mock("@/lib/api/follows", () => ({
  searchArtists: (token: string, q: string) => searchArtists(token, q),
  followArtist: (token: string, id: string) => followArtist(token, id),
  unfollowArtist: (token: string, id: string) => unfollowArtist(token, id),
}));

function user(overrides: Partial<User> = {}): User {
  return {
    id: "u-1",
    email: "a@b.co",
    display_name: null,
    avatar_url: null,
    city_id: null,
    digest_frequency: "weekly",
    genre_preferences: [],
    notification_settings: {},
    spotify_beta_access: false,
    last_login_at: null,
    created_at: "2026-04-20T00:00:00+00:00",
    ...overrides,
  };
}

describe("TasteStep", () => {
  beforeEach(() => {
    listGenres.mockReset();
    updateMe.mockReset();
    searchArtists.mockReset();
    followArtist.mockReset();
    unfollowArtist.mockReset();
    listGenres.mockResolvedValue([
      { slug: "indie-rock", label: "Indie Rock", emoji: "🎸" },
      { slug: "jazz", label: "Jazz", emoji: "🎷" },
    ]);
    searchArtists.mockResolvedValue([]);
    updateMe.mockResolvedValue(user());
    followArtist.mockResolvedValue(undefined);
    unfollowArtist.mockResolvedValue(undefined);
  });

  it("renders the genre catalog pulled from the server", async () => {
    render(
      <TasteStep
        token="jwt"
        user={user()}
        onDone={vi.fn()}
        onSkip={vi.fn()}
        onRefreshUser={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(
      await screen.findByRole("button", { name: /Indie Rock/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Jazz/i })).toBeInTheDocument();
  });

  it("saves selected genres via PATCH /me then advances on Continue", async () => {
    const onDone = vi.fn();
    const refreshUser = vi.fn().mockResolvedValue(undefined);
    render(
      <TasteStep
        token="jwt"
        user={user()}
        onDone={onDone}
        onSkip={vi.fn()}
        onRefreshUser={refreshUser}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Indie Rock/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));

    await waitFor(() =>
      expect(updateMe).toHaveBeenCalledWith("jwt", {
        genre_preferences: ["indie-rock"],
      }),
    );
    expect(refreshUser).toHaveBeenCalled();
    expect(onDone).toHaveBeenCalled();
  });

  it("skips without writing when the user taps Skip for now", async () => {
    const onSkip = vi.fn();
    render(
      <TasteStep
        token="jwt"
        user={user()}
        onDone={vi.fn()}
        onSkip={onSkip}
        onRefreshUser={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /Skip for now/i }),
    );
    expect(onSkip).toHaveBeenCalled();
    expect(updateMe).not.toHaveBeenCalled();
  });

  it("disables Continue until at least one genre or artist is selected", async () => {
    render(
      <TasteStep
        token="jwt"
        user={user()}
        onDone={vi.fn()}
        onSkip={vi.fn()}
        onRefreshUser={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await screen.findByRole("button", { name: /Indie Rock/i });
    expect(screen.getByRole("button", { name: /^Continue$/ })).toBeDisabled();
  });
});
