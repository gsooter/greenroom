/**
 * Tests for EventCard.
 *
 * The card composes several pure-format helpers plus the save button,
 * so these assertions focus on the branches the tile can render:
 * image-vs-placeholder, status badge routing, venue+price visibility,
 * and the full-card Link pointing at the slug URL.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import EventCard from "@/components/events/EventCard";
import type { EventStatus, EventSummary } from "@/types";

// Mock Link + SaveEventButton so we don't have to stand up auth/saved
// context for the pure-card tests.
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

vi.mock("@/components/events/SaveEventButton", () => ({
  __esModule: true,
  default: ({ eventId }: { eventId: string }) => (
    <button type="button" data-testid="save-btn" data-event-id={eventId}>
      save
    </button>
  ),
}));

function summary(overrides: Partial<EventSummary> = {}): EventSummary {
  return {
    id: "e-1",
    title: "Fake Show",
    slug: "fake-show",
    starts_at: "2026-05-02T23:00:00.000Z",
    artists: ["Band A", "Band B"],
    genres: [],
    image_url: "https://img.test/e.jpg",
    min_price: 25,
    max_price: 55,
    prices_refreshed_at: null,
    status: "confirmed",
    venue: {
      id: "v-1",
      name: "Black Cat",
      slug: "black-cat",
      city: {
        id: "c-1",
        name: "Washington",
        slug: "washington-dc",
        state: "DC",
        region: "DMV",
      },
    },
    ...overrides,
  };
}

describe("EventCard", () => {
  it("links to /events/<slug> with the title as aria-label", () => {
    const { container } = render(<EventCard event={summary()} />);
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/events/fake-show");
    expect(link?.getAttribute("aria-label")).toBe("Fake Show");
  });

  it("renders title, venue, artists, and a price range", () => {
    render(<EventCard event={summary()} />);
    expect(screen.getByText("Fake Show")).toBeInTheDocument();
    expect(screen.getByText("Black Cat")).toBeInTheDocument();
    expect(screen.getByText(/Band A/)).toBeInTheDocument();
    expect(screen.getByText("$25–$55")).toBeInTheDocument();
  });

  it("omits price when both min and max are null", () => {
    render(
      <EventCard
        event={summary({ min_price: null, max_price: null })}
      />,
    );
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
  });

  it("renders a freshness caption when prices were recently refreshed", () => {
    const recently = new Date(Date.now() - 90 * 60 * 1000).toISOString();
    render(<EventCard event={summary({ prices_refreshed_at: recently })} />);
    expect(screen.getByText(/^Updated /)).toBeInTheDocument();
  });

  it("omits the freshness caption when prices_refreshed_at is null", () => {
    render(<EventCard event={summary({ prices_refreshed_at: null })} />);
    expect(screen.queryByText(/^Updated /)).not.toBeInTheDocument();
  });

  it("renders an image background when image_url is present", () => {
    const { container } = render(<EventCard event={summary()} />);
    const bg = container.querySelector('[role="presentation"]') as HTMLElement;
    expect(bg.style.backgroundImage).toContain("https://img.test/e.jpg");
  });

  it("renders a plain placeholder when image_url is null", () => {
    const { container } = render(
      <EventCard event={summary({ image_url: null })} />,
    );
    const bg = container.querySelector('[role="presentation"]') as HTMLElement;
    expect(bg.style.backgroundImage).toBe("");
  });

  it.each<EventStatus>([
    "announced",
    "on_sale",
    "confirmed",
    "sold_out",
    "cancelled",
    "postponed",
  ])("renders a status badge for %s", (status) => {
    render(<EventCard event={summary({ status })} />);
    const label = {
      announced: "Announced",
      on_sale: "On sale",
      confirmed: "Confirmed",
      sold_out: "Sold out",
      cancelled: "Cancelled",
      postponed: "Postponed",
    }[status];
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("hides the venue block entirely when venue is null", () => {
    render(<EventCard event={summary({ venue: null })} />);
    expect(screen.queryByText("Black Cat")).not.toBeInTheDocument();
    expect(screen.queryByText("Washington, DC")).not.toBeInTheDocument();
  });

  it("hides the artists line when the list is empty", () => {
    render(<EventCard event={summary({ artists: [] })} />);
    expect(screen.queryByText(/Band/)).not.toBeInTheDocument();
  });

  it("wires the save button to the event id", () => {
    render(<EventCard event={summary()} />);
    expect(screen.getByTestId("save-btn").dataset.eventId).toBe("e-1");
  });
});
