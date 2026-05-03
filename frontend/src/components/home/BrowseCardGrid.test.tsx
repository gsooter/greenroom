/**
 * Tests for BrowseCardGrid.
 *
 * The grid is a thin client wrapper over EventCard whose only logic
 * is "swap layout when the compact preference flips". EventCard
 * itself is mocked so these tests focus on the layout selection
 * rather than re-asserting card internals.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BrowseCardGrid from "@/components/home/BrowseCardGrid";
import type { EventSummary } from "@/types";

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

function summary(id: string): EventSummary {
  return {
    id,
    title: `Show ${id}`,
    slug: `show-${id}`,
    starts_at: "2026-06-01T00:00:00Z",
    artists: [],
    genres: [],
    image_url: null,
    min_price: null,
    max_price: null,
    prices_refreshed_at: null,
    status: "confirmed",
    venue: null,
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("BrowseCardGrid", () => {
  it("renders the comfortable grid by default", () => {
    render(<BrowseCardGrid events={[summary("a"), summary("b")]} />);
    const grid = screen.getByTestId("home-browse-grid");
    expect(grid).toHaveAttribute("data-compact", "false");
    expect(grid.tagName.toLowerCase()).toBe("div");
    const cards = screen.getAllByTestId("event-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveAttribute("data-compact", "false");
  });

  it("renders the compact list when the preference is set", async () => {
    window.localStorage.setItem("greenroom.home.compact", "true");
    await act(async () => {
      render(<BrowseCardGrid events={[summary("a")]} />);
    });
    const grid = screen.getByTestId("home-browse-grid");
    expect(grid).toHaveAttribute("data-compact", "true");
    expect(grid.tagName.toLowerCase()).toBe("ul");
    expect(screen.getByTestId("event-card")).toHaveAttribute(
      "data-compact",
      "true",
    );
  });
});
