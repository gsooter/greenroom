/**
 * Tests for the followed-artists and followed-venues sections.
 *
 * Covers: empty-state copy, populated lists, optimistic unfollow,
 * and revert-on-failure for both lists.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FollowingSections } from "@/components/settings/FollowingSections";
import type { ArtistSummary, VenueSummary } from "@/types";

const listFollowedArtists = vi.fn();
const listFollowedVenues = vi.fn();
const unfollowArtist = vi.fn();
const unfollowVenue = vi.fn();

vi.mock("@/lib/api/follows", () => ({
  listFollowedArtists: (token: string) => listFollowedArtists(token),
  listFollowedVenues: (token: string) => listFollowedVenues(token),
  unfollowArtist: (token: string, id: string) => unfollowArtist(token, id),
  unfollowVenue: (token: string, id: string) => unfollowVenue(token, id),
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function paginated<T>(items: T[]): {
  data: T[];
  meta: { total: number; page: number; per_page: number; has_next: boolean };
} {
  return {
    data: items,
    meta: { total: items.length, page: 1, per_page: 50, has_next: false },
  };
}

const PHOEBE: ArtistSummary = {
  id: "a-1",
  name: "Phoebe Bridgers",
  genres: ["indie", "folk"],
  is_followed: true,
};

const BLACK_CAT: VenueSummary = {
  id: "v-1",
  name: "Black Cat",
  slug: "black-cat",
  address: "1811 14th St NW",
  image_url: null,
  tags: [],
  city: {
    id: "c-1",
    name: "Washington",
    slug: "dc",
    state: "DC",
    region: "dmv",
  },
};

describe("FollowingSections", () => {
  beforeEach(() => {
    listFollowedArtists.mockReset();
    listFollowedVenues.mockReset();
    unfollowArtist.mockReset();
    unfollowVenue.mockReset();
  });

  it("renders empty-state copy when neither list has rows", async () => {
    listFollowedArtists.mockResolvedValueOnce(paginated([]));
    listFollowedVenues.mockResolvedValueOnce(paginated([]));

    render(<FollowingSections token="jwt" />);

    expect(
      await screen.findByText(/aren't following any artists yet/i),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/aren't following any venues yet/i),
    ).toBeInTheDocument();
  });

  it("renders followed artists with a working unfollow button", async () => {
    listFollowedArtists.mockResolvedValueOnce(paginated([PHOEBE]));
    listFollowedVenues.mockResolvedValueOnce(paginated([]));
    unfollowArtist.mockResolvedValueOnce(undefined);

    render(<FollowingSections token="jwt" />);

    const unfollowBtn = await screen.findByRole("button", {
      name: /unfollow/i,
    });
    fireEvent.click(unfollowBtn);

    await waitFor(() => {
      expect(unfollowArtist).toHaveBeenCalledWith("jwt", PHOEBE.id);
    });
    expect(screen.queryByText("Phoebe Bridgers")).not.toBeInTheDocument();
  });

  it("reverts the artists list when unfollow fails", async () => {
    listFollowedArtists.mockResolvedValueOnce(paginated([PHOEBE]));
    listFollowedVenues.mockResolvedValueOnce(paginated([]));
    unfollowArtist.mockRejectedValueOnce(new Error("network"));

    render(<FollowingSections token="jwt" />);

    const unfollowBtn = await screen.findByRole("button", {
      name: /unfollow/i,
    });
    fireEvent.click(unfollowBtn);

    expect(
      await screen.findByText(/could not unfollow that artist/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Phoebe Bridgers")).toBeInTheDocument();
  });

  it("renders followed venues and unfollows optimistically", async () => {
    listFollowedArtists.mockResolvedValueOnce(paginated([]));
    listFollowedVenues.mockResolvedValueOnce(paginated([BLACK_CAT]));
    unfollowVenue.mockResolvedValueOnce(undefined);

    render(<FollowingSections token="jwt" />);

    expect(await screen.findByText("Black Cat")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /unfollow/i }));

    await waitFor(() => {
      expect(unfollowVenue).toHaveBeenCalledWith("jwt", BLACK_CAT.id);
    });
    expect(screen.queryByText("Black Cat")).not.toBeInTheDocument();
  });
});
