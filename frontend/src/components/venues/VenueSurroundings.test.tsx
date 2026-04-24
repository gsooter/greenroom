/**
 * Tests for VenueSurroundings.
 *
 * Exercises the card's external surface: tab switching between Tips and
 * Nearby, the "Leave a tip" toggle gated on authentication, list
 * rendering of pre-fetched POIs, opening the expanded map modal, and
 * the vote-with-rollback flow that we preserve from the previous
 * VenueMapTips implementation.
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VenueSurroundings from "@/components/venues/VenueSurroundings";
import type { NearbyPoi, VenueMapSnapshot } from "@/lib/api/venues";
import type { MapRecommendation } from "@/types";

const listTipsMock = vi.fn();
const voteMock = vi.fn();
const submitMock = vi.fn();
const searchPlacesMock = vi.fn();
const showToast = vi.fn();

let mockAuth: {
  isAuthenticated: boolean;
  isLoading: boolean;
  token: string | null;
} = { isAuthenticated: false, isLoading: false, token: null };

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ show: showToast }),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/guest-session", () => ({
  getGuestSessionId: () => "guest-test",
}));

vi.mock("@/lib/api/maps", () => ({
  listVenueTips: (...args: unknown[]) => listTipsMock(...args),
  voteOnMapRecommendation: (...args: unknown[]) => voteMock(...args),
  submitMapRecommendation: (...args: unknown[]) => submitMock(...args),
  searchNearbyPlaces: (...args: unknown[]) => searchPlacesMock(...args),
  getMapKitToken: vi.fn(),
}));

// The modal pulls in MapKit JS. We don't exercise it in these tests —
// just confirm it mounts, so stub it with a marker div.
vi.mock("@/components/venues/VenueSurroundingsModal", () => ({
  default: ({ venueName }: { venueName: string }) => (
    <div data-testid="surroundings-modal">Modal for {venueName}</div>
  ),
}));

function makeTip(partial: Partial<MapRecommendation> = {}): MapRecommendation {
  return {
    id: "tip-1",
    venue_id: "venue-uuid",
    place_name: "El Taco Lab",
    place_address: "2000 14th St NW",
    latitude: 38.9172,
    longitude: -77.0315,
    category: "food",
    body: "Best tacos before the show.",
    likes: 3,
    dislikes: 1,
    viewer_vote: null,
    suppressed: false,
    created_at: new Date().toISOString(),
    distance_from_venue_m: 120,
    ...partial,
  };
}

function makePoi(partial: Partial<NearbyPoi> = {}): NearbyPoi {
  return {
    name: "Black Cat Bar",
    category: "Bar",
    address: "1811 14th St NW",
    latitude: 38.9176,
    longitude: -77.0318,
    distance_m: 40,
    ...partial,
  };
}

const SNAPSHOT: VenueMapSnapshot = {
  url: "https://example.test/snapshot.png",
  width: 800,
  height: 280,
};

function renderCard(overrides: {
  tips?: MapRecommendation[];
  nearbyPois?: NearbyPoi[];
  snapshot?: VenueMapSnapshot | null;
} = {}) {
  return render(
    <VenueSurroundings
      slug="black-cat"
      venueId="venue-uuid"
      venueName="Black Cat"
      venueAddress="1811 14th St NW"
      latitude={38.9176}
      longitude={-77.0318}
      snapshot={overrides.snapshot === undefined ? SNAPSHOT : overrides.snapshot}
      nearbyPois={overrides.nearbyPois ?? [makePoi()]}
    />,
  );
}

describe("VenueSurroundings", () => {
  beforeEach(() => {
    listTipsMock.mockReset();
    voteMock.mockReset();
    submitMock.mockReset();
    searchPlacesMock.mockReset();
    showToast.mockReset();
    mockAuth = { isAuthenticated: false, isLoading: false, token: null };
  });

  it("renders the snapshot and opens the modal when clicked", async () => {
    listTipsMock.mockResolvedValueOnce([]);

    renderCard();

    await waitFor(() => expect(listTipsMock).toHaveBeenCalled());
    fireEvent.click(
      screen.getByRole("button", {
        name: /Open interactive map around Black Cat/i,
      }),
    );
    expect(screen.getByTestId("surroundings-modal")).toBeInTheDocument();
  });

  it("renders fetched tips on the Tips tab", async () => {
    listTipsMock.mockResolvedValueOnce([makeTip()]);

    renderCard();

    await screen.findByText("El Taco Lab");
    expect(screen.getByText("Best tacos before the show.")).toBeInTheDocument();
  });

  it("shows an empty state when there are no tips", async () => {
    listTipsMock.mockResolvedValueOnce([]);

    renderCard();

    await screen.findByText("No tips yet");
  });

  it("switches to Nearby and lists the pre-fetched POIs", async () => {
    listTipsMock.mockResolvedValueOnce([]);

    renderCard({ nearbyPois: [makePoi(), makePoi({ name: "Kinfolk" })] });

    await waitFor(() => expect(listTipsMock).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: /Nearby/i }));

    expect(screen.getByText("Black Cat Bar")).toBeInTheDocument();
    expect(screen.getByText("Kinfolk")).toBeInTheDocument();
  });

  it("gates the 'Leave a tip' button behind auth", async () => {
    listTipsMock.mockResolvedValue([]);

    renderCard();

    await waitFor(() => expect(listTipsMock).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: /Leave a tip/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Sign in to drop a tip/i)).toBeInTheDocument();
  });

  it("toggles the tip form for signed-in users", async () => {
    mockAuth = {
      isAuthenticated: true,
      isLoading: false,
      token: "access-token",
    };
    listTipsMock.mockResolvedValue([]);

    renderCard();

    await waitFor(() => expect(listTipsMock).toHaveBeenCalled());
    const toggle = screen.getByRole("button", { name: /Leave a tip/i });
    expect(screen.queryByLabelText("Place name")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByLabelText("Place name")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(screen.queryByLabelText("Place name")).not.toBeInTheDocument();
  });

  it("applies an optimistic upvote and confirms with server counts", async () => {
    const tip = makeTip({ likes: 3, dislikes: 1, viewer_vote: null });
    listTipsMock.mockResolvedValueOnce([tip]);
    voteMock.mockResolvedValueOnce({
      likes: 4,
      dislikes: 1,
      viewer_vote: 1,
      suppressed: false,
    });

    renderCard();

    await screen.findByText(tip.place_name);
    fireEvent.click(screen.getByRole("button", { name: "Upvote" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Upvote" }).getAttribute(
          "aria-pressed",
        ),
      ).toBe("true");
    });
    expect(voteMock).toHaveBeenCalledWith(
      "tip-1",
      null,
      1,
      "guest-test",
    );
  });

  it("rolls back the optimistic vote on error", async () => {
    const tip = makeTip({ likes: 3, dislikes: 1, viewer_vote: null });
    listTipsMock.mockResolvedValueOnce([tip]);
    voteMock.mockRejectedValueOnce(new Error("offline"));

    renderCard();

    await screen.findByText(tip.place_name);
    fireEvent.click(screen.getByRole("button", { name: "Upvote" }));

    await waitFor(() => expect(showToast).toHaveBeenCalled());
    expect(
      screen.getByRole("button", { name: "Upvote" }).getAttribute(
        "aria-pressed",
      ),
    ).toBe("false");
  });

  it("omits the snapshot when the backend returns null", async () => {
    listTipsMock.mockResolvedValueOnce([]);

    renderCard({ snapshot: null });

    await waitFor(() => expect(listTipsMock).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", {
        name: /Open interactive map/i,
      }),
    ).not.toBeInTheDocument();
    // The "View on map" shortcut in the tab strip is still available.
    expect(
      screen.getByRole("button", { name: /View on map/i }),
    ).toBeInTheDocument();
  });
});
