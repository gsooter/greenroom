/**
 * Tests for PersonalizedHome.
 *
 * Verifies branching by auth state and ``has_signal``, including the
 * skeleton-while-loading path, the welcome prompt for zero-signal
 * users, the new-since hide rule, the NEW badge, and the recommendation
 * reasons coming through to the rendered chips.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import PersonalizedHome from "@/components/home/PersonalizedHome";
import type { HomePayload, Recommendation, EventSummary, User } from "@/types";

const getHomeMock = vi.fn<(token: string) => Promise<HomePayload>>();
vi.mock("@/lib/api/home", () => ({
  getHome: (...args: unknown[]) => (getHomeMock as unknown as Mock)(...args),
}));

const useAuthMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  useAuth: () => useAuthMock(),
}));

vi.mock("@/components/events/EventCard", () => ({
  __esModule: true,
  default: ({
    event,
    compact,
  }: {
    event: { id: string; title: string };
    compact?: boolean;
  }) => (
    <div data-testid="event-card" data-compact={compact ? "true" : "false"}>
      {event.title}
    </div>
  ),
}));

vi.mock("@/components/recommendations/RecommendationCard", () => ({
  __esModule: true,
  default: ({
    recommendation,
    compact,
  }: {
    recommendation: Recommendation;
    compact?: boolean;
  }) => (
    <div
      data-testid="recommendation-card"
      data-compact={compact ? "true" : "false"}
    >
      <span>{recommendation.event.title}</span>
      <ul>
        {recommendation.match_reasons.map((r) => (
          <li key={r.label}>{r.label}</li>
        ))}
      </ul>
    </div>
  ),
}));

vi.mock("@/components/recommendations/RecommendationGridSkeleton", () => ({
  __esModule: true,
  default: () => <div data-testid="rec-skeleton" />,
}));

function buildEvent(overrides: Partial<EventSummary> = {}): EventSummary {
  return {
    id: overrides.id ?? "e1",
    title: overrides.title ?? "Test Show",
    slug: "test-show",
    starts_at: "2026-06-01T00:00:00Z",
    artists: ["Phoebe Bridgers"],
    genres: [],
    image_url: null,
    min_price: null,
    max_price: null,
    prices_refreshed_at: null,
    status: "confirmed",
    venue: null,
    ...overrides,
  };
}

function buildRecommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    id: overrides.id ?? "rec-1",
    score: 0.9,
    generated_at: null,
    is_dismissed: false,
    match_reasons: [
      {
        scorer: "artist_match",
        kind: "spotify_id",
        label: "You listen to Phoebe Bridgers",
        artist_name: "Phoebe Bridgers",
      },
    ],
    score_breakdown: {},
    event: buildEvent({ id: "rec-event-1", title: "Phoebe Bridgers @ The Anthem" }),
    ...overrides,
  };
}

function payloadWith(overrides: Partial<HomePayload> = {}): HomePayload {
  return {
    has_signal: true,
    last_home_visit_at: "2026-05-01T12:00:00Z",
    recommendations: [],
    popularity_fallback: [],
    new_since_last_visit: [],
    ...overrides,
  };
}

const mockUser: User = {
  id: "u-1",
  email: "u@example.test",
  display_name: "Pat",
  avatar_url: null,
  city_id: null,
  digest_frequency: "weekly",
  genre_preferences: [],
  notification_settings: {},
  spotify_beta_access: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  getHomeMock.mockReset();
  useAuthMock.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("PersonalizedHome", () => {
  it("renders nothing when the visitor is not authenticated", () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      token: null,
      user: null,
    });
    const { container } = render(<PersonalizedHome />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the welcome prompt when the user has no signal", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(payloadWith({ has_signal: false }));
    render(<PersonalizedHome />);

    await waitFor(() => {
      expect(screen.getByTestId("home-section-welcome")).toBeInTheDocument();
    });
    expect(screen.getByText("Welcome, Pat")).toBeInTheDocument();
    expect(screen.queryByTestId("home-section-recs")).toBeNull();
    expect(screen.queryByTestId("home-section-new")).toBeNull();
  });

  it("renders Section 1 with reasons and Section 2 with NEW badges when signal present", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    const recs = [
      buildRecommendation({
        id: "rec-A",
        match_reasons: [
          {
            scorer: "artist_match",
            kind: "artist_name",
            label: "You listen to Phoebe Bridgers",
            artist_name: "Phoebe Bridgers",
          },
        ],
        event: buildEvent({ id: "rec-A-evt", title: "Phoebe @ Anthem" }),
      }),
      buildRecommendation({
        id: "rec-B",
        match_reasons: [
          {
            scorer: "similar_artist",
            kind: "similar_artist",
            label: "Similar to Soccer Mommy",
            artist_name: "Snail Mail",
          },
        ],
        event: buildEvent({ id: "rec-B-evt", title: "Snail Mail @ DC9" }),
      }),
      buildRecommendation({
        id: "rec-C",
        match_reasons: [
          {
            scorer: "artist_match",
            kind: "genre_overlap",
            label: "Matches genre: Indie Rock",
            genre: "Indie Rock",
          },
        ],
        event: buildEvent({ id: "rec-C-evt", title: "Big Thief @ 9:30 Club" }),
      }),
    ];
    const newSince = [
      buildEvent({ id: "n1", title: "New Indigo De Souza Show" }),
      buildEvent({ id: "n2", title: "New Soccer Mommy Show" }),
    ];

    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: recs,
        new_since_last_visit: newSince,
      }),
    );

    render(<PersonalizedHome />);

    await waitFor(() => {
      expect(screen.getByTestId("home-section-recs")).toBeInTheDocument();
    });

    const recsSection = screen.getByTestId("home-section-recs");
    expect(within(recsSection).getByText("You listen to Phoebe Bridgers")).toBeInTheDocument();
    expect(within(recsSection).getByText("Similar to Soccer Mommy")).toBeInTheDocument();

    const newSection = screen.getByTestId("home-section-new");
    expect(within(newSection).getByText("New Indigo De Souza Show")).toBeInTheDocument();
    expect(within(newSection).getAllByTestId("home-new-badge")).toHaveLength(2);
  });

  it("hides the new-since section when there are zero new events", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "rec-X" }),
          buildRecommendation({
            id: "rec-Y",
            event: buildEvent({ id: "evt-Y", title: "Show Y" }),
          }),
          buildRecommendation({
            id: "rec-Z",
            event: buildEvent({ id: "evt-Z", title: "Show Z" }),
          }),
        ],
        new_since_last_visit: [],
      }),
    );

    render(<PersonalizedHome />);

    await waitFor(() => {
      expect(screen.getByTestId("home-section-recs")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("home-section-new")).toBeNull();
  });

  it("shows the See-all link when more than four new events are returned", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    const newSince = Array.from({ length: 6 }).map((_, i) =>
      buildEvent({ id: `n-${i}`, title: `New Show ${i}` }),
    );
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "rec-A" }),
          buildRecommendation({ id: "rec-B" }),
          buildRecommendation({ id: "rec-C" }),
        ],
        new_since_last_visit: newSince,
      }),
    );
    render(<PersonalizedHome />);
    await waitFor(() => {
      expect(screen.getByTestId("home-section-new")).toBeInTheDocument();
    });
    expect(screen.getByText("See all (6) →")).toBeInTheDocument();
  });

  it("renders the thin-signal prompt when total recs are under three", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [buildRecommendation({ id: "rec-only" })],
        popularity_fallback: [],
      }),
    );
    render(<PersonalizedHome />);
    await waitFor(() => {
      expect(screen.getByTestId("home-section-thin-signal")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("home-section-recs")).toBeNull();
  });

  it("renders the new-since section above the recommendations section", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "rec-A" }),
          buildRecommendation({
            id: "rec-B",
            event: buildEvent({ id: "rec-B-evt", title: "Show B" }),
          }),
          buildRecommendation({
            id: "rec-C",
            event: buildEvent({ id: "rec-C-evt", title: "Show C" }),
          }),
        ],
        new_since_last_visit: [buildEvent({ id: "n1", title: "Newly Announced" })],
      }),
    );

    render(<PersonalizedHome />);

    await waitFor(() => {
      expect(screen.getByTestId("home-section-new")).toBeInTheDocument();
    });

    const newSection = screen.getByTestId("home-section-new");
    const recsSection = screen.getByTestId("home-section-recs");
    // Document order: new-since must come before recommendations.
    expect(
      newSection.compareDocumentPosition(recsSection) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("threads the compact preference through to recommendation and event cards", async () => {
    window.localStorage.setItem("greenroom.home.compact", "true");
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "rec-A" }),
          buildRecommendation({
            id: "rec-B",
            event: buildEvent({ id: "evt-B", title: "Show B" }),
          }),
          buildRecommendation({
            id: "rec-C",
            event: buildEvent({ id: "evt-C", title: "Show C" }),
          }),
        ],
        new_since_last_visit: [buildEvent({ id: "n1", title: "New Show" })],
      }),
    );

    render(<PersonalizedHome />);

    await waitFor(() => {
      expect(screen.getByTestId("home-section-recs")).toBeInTheDocument();
    });
    const recCards = screen.getAllByTestId("recommendation-card");
    expect(recCards.length).toBeGreaterThan(0);
    recCards.forEach((card) =>
      expect(card).toHaveAttribute("data-compact", "true"),
    );
    expect(
      within(screen.getByTestId("home-section-new")).getByTestId("event-card"),
    ).toHaveAttribute("data-compact", "true");
  });

  it("renders the compact-mode toggle when the personalized layout shows", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    getHomeMock.mockResolvedValue(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "r1" }),
          buildRecommendation({
            id: "r2",
            event: buildEvent({ id: "e2", title: "Show 2" }),
          }),
          buildRecommendation({
            id: "r3",
            event: buildEvent({ id: "e3", title: "Show 3" }),
          }),
        ],
      }),
    );
    render(<PersonalizedHome />);
    await waitFor(() =>
      expect(screen.getByTestId("home-compact-toggle")).toBeInTheDocument(),
    );
  });

  it("shows the skeleton while the home payload is in flight", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
      user: mockUser,
    });
    let resolve!: (payload: HomePayload) => void;
    getHomeMock.mockReturnValue(
      new Promise<HomePayload>((res) => {
        resolve = res;
      }),
    );
    render(<PersonalizedHome />);
    expect(screen.getByTestId("rec-skeleton")).toBeInTheDocument();

    resolve(
      payloadWith({
        has_signal: true,
        recommendations: [
          buildRecommendation({ id: "r1" }),
          buildRecommendation({ id: "r2" }),
          buildRecommendation({ id: "r3" }),
        ],
      }),
    );
    await waitFor(() => {
      expect(screen.queryByTestId("rec-skeleton")).toBeNull();
    });
  });
});
