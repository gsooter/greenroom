/**
 * Tests for RecommendationCard.
 *
 * EventCard is mocked so these focus on the chip behavior — dedupe,
 * cap at three, and the new venue-affinity reason kind landing on
 * blush styling like every other reason chip.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import RecommendationCard from "@/components/recommendations/RecommendationCard";
import type { Recommendation, RecommendationMatchReason } from "@/types";

vi.mock("@/components/events/EventCard", () => ({
  __esModule: true,
  default: ({ event }: { event: { id: string; title: string } }) => (
    <div data-testid="event-card" data-event-id={event.id}>
      {event.title}
    </div>
  ),
}));

function reason(
  overrides: Partial<RecommendationMatchReason> = {},
): RecommendationMatchReason {
  return {
    scorer: "artist_match",
    kind: "spotify_id",
    label: "You listen to Phoebe Bridgers",
    artist_name: "Phoebe Bridgers",
    ...overrides,
  };
}

function buildRec(reasons: RecommendationMatchReason[]): Recommendation {
  return {
    id: "rec-1",
    score: 0.9,
    generated_at: null,
    is_dismissed: false,
    match_reasons: reasons,
    score_breakdown: {},
    event: {
      id: "e-1",
      title: "Phoebe Bridgers @ The Anthem",
      slug: "phoebe",
      starts_at: "2026-05-02T23:00:00Z",
      artists: ["Phoebe Bridgers"],
      genres: [],
      image_url: null,
      min_price: null,
      max_price: null,
      status: "confirmed",
      venue: null,
    },
  };
}

describe("RecommendationCard", () => {
  it("renders the embedded event card", () => {
    render(<RecommendationCard recommendation={buildRec([reason()])} />);
    expect(screen.getByTestId("event-card")).toHaveTextContent(
      "Phoebe Bridgers @ The Anthem",
    );
  });

  it("renders one chip per reason", () => {
    const recommendation = buildRec([
      reason({ artist_name: "A", label: "You listen to A" }),
      reason({ artist_name: "B", label: "You listen to B" }),
    ]);
    render(<RecommendationCard recommendation={recommendation} />);
    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  });

  it("dedupes reasons that share an identity key", () => {
    const recommendation = buildRec([
      reason({ artist_name: "Same", label: "You listen to Same" }),
      reason({ artist_name: "Same", label: "You listen to Same" }),
    ]);
    render(<RecommendationCard recommendation={recommendation} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("caps chips at three", () => {
    const recommendation = buildRec([
      reason({ artist_name: "A", label: "You listen to A" }),
      reason({ artist_name: "B", label: "You listen to B" }),
      reason({ artist_name: "C", label: "You listen to C" }),
      reason({ artist_name: "D", label: "You listen to D" }),
    ]);
    render(<RecommendationCard recommendation={recommendation} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("renders a saved-venue reason chip with the venue name as the dedupe key", () => {
    const recommendation = buildRec([
      reason({
        scorer: "venue_affinity",
        kind: "saved_venue",
        label: "You've saved shows at Black Cat",
        venue_name: "Black Cat",
        artist_name: undefined,
      }),
      // Same venue surfaced twice → only one chip.
      reason({
        scorer: "venue_affinity",
        kind: "saved_venue",
        label: "You've saved shows at Black Cat",
        venue_name: "Black Cat",
        artist_name: undefined,
      }),
    ]);
    render(<RecommendationCard recommendation={recommendation} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("You've saved shows at Black Cat");
  });

  it("renders no chip list when there are zero reasons", () => {
    render(<RecommendationCard recommendation={buildRec([])} />);
    expect(screen.queryByRole("list")).toBeNull();
  });
});
