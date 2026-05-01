/**
 * Tests for MeDashboard.
 *
 * The /me page consolidates Your Picks, Saved Shows, Followed Artists,
 * Followed Venues, and Account actions onto a single screen so the new
 * 4-tab mobile bottom nav can collapse three previous routes into one
 * destination. These tests cover each of those zones plus the sign-out
 * affordance.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MeDashboard from "@/components/me/MeDashboard";
import type { EventSummary, Recommendation, SavedEvent, User } from "@/types";

const mockReplace = vi.fn();
const mockLogout = vi.fn();
const mockListRecommendations = vi.fn();
const mockListFollowedArtists = vi.fn();
const mockListFollowedVenues = vi.fn();

interface MockSavedEventsState {
  savedEvents: SavedEvent[];
  isReady: boolean;
}

let mockSaved: MockSavedEventsState = {
  savedEvents: [],
  isReady: true,
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/me",
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

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", email: "fan@example.com" } as User,
    isAuthenticated: true,
    isLoading: false,
    token: "tok",
    logout: mockLogout,
  }),
}));

vi.mock("@/lib/saved-events-context", () => ({
  useSavedEvents: () => mockSaved,
}));

vi.mock("@/lib/api/recommendations", () => ({
  listRecommendations: (...args: unknown[]) => mockListRecommendations(...args),
}));

vi.mock("@/lib/api/follows", () => ({
  listFollowedArtists: (...args: unknown[]) =>
    mockListFollowedArtists(...args),
  listFollowedVenues: (...args: unknown[]) => mockListFollowedVenues(...args),
  unfollowArtist: vi.fn(),
  unfollowVenue: vi.fn(),
}));

vi.mock("@/components/events/EventCard", () => ({
  __esModule: true,
  default: ({ event }: { event: EventSummary }) => (
    <div data-testid="event-card">{event.title}</div>
  ),
}));

vi.mock("@/components/recommendations/RecommendationCard", () => ({
  __esModule: true,
  default: ({ recommendation }: { recommendation: Recommendation }) => (
    <div data-testid="rec-card">{recommendation.event.title}</div>
  ),
}));

function eventFixture(id: string, title: string): EventSummary {
  return {
    id,
    title,
    slug: id,
    starts_at: "2026-06-01T20:00:00Z",
    artists: ["Headliner"],
    genres: ["indie"],
    image_url: null,
    min_price: null,
    max_price: null,
    prices_refreshed_at: null,
    status: "on_sale",
    venue: null,
  } as unknown as EventSummary;
}

function recommendationFixture(id: string, title: string): Recommendation {
  return {
    id,
    score: 0.9,
    generated_at: null,
    is_dismissed: false,
    match_reasons: [],
    score_breakdown: {},
    event: eventFixture(id, title),
  };
}

function savedEventFixture(id: string, title: string): SavedEvent {
  return {
    id,
    saved_at: "2026-04-01T00:00:00Z",
    event: eventFixture(id, title),
  } as unknown as SavedEvent;
}

function paginated<T>(items: T[]): {
  data: T[];
  meta: { total: number; page: number; per_page: number; has_next: boolean };
} {
  return {
    data: items,
    meta: {
      total: items.length,
      page: 1,
      per_page: 20,
      has_next: false,
    },
  };
}

describe("MeDashboard", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    mockLogout.mockReset();
    mockListRecommendations.mockReset();
    mockListFollowedArtists.mockReset();
    mockListFollowedVenues.mockReset();
    mockListFollowedArtists.mockResolvedValue(paginated([]));
    mockListFollowedVenues.mockResolvedValue(paginated([]));
    mockSaved = { savedEvents: [], isReady: true };
  });

  it("greets the user by display name", () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    render(<MeDashboard displayName="Fan" token="tok" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Hey, Fan",
    );
  });

  it("renders top picks once the recommendation fetch resolves", async () => {
    mockListRecommendations.mockResolvedValue(
      paginated([
        recommendationFixture("r-1", "Show A"),
        recommendationFixture("r-2", "Show B"),
      ]),
    );
    render(<MeDashboard displayName="Fan" token="tok" />);
    await waitFor(() => {
      expect(screen.getAllByTestId("rec-card")).toHaveLength(2);
    });
    expect(screen.getByText("Show A")).toBeInTheDocument();
    expect(screen.getByText("Show B")).toBeInTheDocument();
  });

  it("shows an empty hint when recommendations come back empty", async () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    render(<MeDashboard displayName="Fan" token="tok" />);
    await waitFor(() => {
      expect(
        screen.getByText(/Connect Spotify or pick a few genres/i),
      ).toBeInTheDocument();
    });
  });

  it("surfaces an error when the picks fetch fails", async () => {
    mockListRecommendations.mockRejectedValue(new Error("nope"));
    render(<MeDashboard displayName="Fan" token="tok" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /couldn't load your picks/i,
      );
    });
  });

  it("renders a saved-shows preview when the user has saved events", async () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    mockSaved = {
      savedEvents: [
        savedEventFixture("e-1", "Saved One"),
        savedEventFixture("e-2", "Saved Two"),
      ],
      isReady: true,
    };
    render(<MeDashboard displayName="Fan" token="tok" />);
    expect(screen.getByText("Saved One")).toBeInTheDocument();
    expect(screen.getByText("Saved Two")).toBeInTheDocument();
  });

  it("caps the saved-shows preview at three and links to the full list", async () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    mockSaved = {
      savedEvents: [
        savedEventFixture("e-1", "Saved One"),
        savedEventFixture("e-2", "Saved Two"),
        savedEventFixture("e-3", "Saved Three"),
        savedEventFixture("e-4", "Saved Four"),
      ],
      isReady: true,
    };
    render(<MeDashboard displayName="Fan" token="tok" />);
    expect(screen.getAllByTestId("event-card")).toHaveLength(3);
    expect(
      screen.getByRole("link", { name: /see all saved/i }),
    ).toHaveAttribute("href", "/saved");
  });

  it("links Settings and triggers logout + redirect on Sign out", () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    render(<MeDashboard displayName="Fan" token="tok" />);
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(mockLogout).toHaveBeenCalledOnce();
    expect(mockReplace).toHaveBeenCalledWith("/");
  });

  it("renders the Followed sections so artists and venues are reachable", async () => {
    mockListRecommendations.mockResolvedValue(paginated([]));
    render(<MeDashboard displayName="Fan" token="tok" />);
    await waitFor(() => {
      expect(mockListFollowedArtists).toHaveBeenCalled();
      expect(mockListFollowedVenues).toHaveBeenCalled();
    });
    expect(screen.getByText("Followed artists")).toBeInTheDocument();
    expect(screen.getByText("Followed venues")).toBeInTheDocument();
  });
});
