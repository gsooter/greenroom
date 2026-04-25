/**
 * Tests for the active-filter chip row.
 *
 * The row is server-rendered: each chip is a plain anchor whose href
 * drops just that dimension from the URL. Verify chip composition,
 * label resolution, and the "Clear all" link.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ActiveFilterChips from "@/components/events/ActiveFilterChips";
import { EMPTY_FILTERS } from "@/lib/event-filters";

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

const VENUES = { "v-1": "9:30 Club", "v-2": "Black Cat" };
const GENRES = { indie: "Indie", folk: "Folk" };

describe("ActiveFilterChips", () => {
  it("renders nothing when no filters are active", () => {
    const { container } = render(
      <ActiveFilterChips
        filters={EMPTY_FILTERS}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per active dimension and resolves labels", () => {
    render(
      <ActiveFilterChips
        filters={{
          ...EMPTY_FILTERS,
          genres: ["indie"],
          venueIds: ["v-1"],
          artistSearch: "phoebe",
          priceMax: 30,
          availableOnly: true,
        }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.getByText("Genre: Indie")).toBeInTheDocument();
    expect(screen.getByText("Venue: 9:30 Club")).toBeInTheDocument();
    expect(screen.getByText("Artist: phoebe")).toBeInTheDocument();
    expect(screen.getByText("Under $30")).toBeInTheDocument();
    expect(screen.getByText("Available only")).toBeInTheDocument();
  });

  it("collapses multiple genres into a comma-joined chip", () => {
    render(
      <ActiveFilterChips
        filters={{ ...EMPTY_FILTERS, genres: ["indie", "folk"] }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.getByText("Genres: Indie, Folk")).toBeInTheDocument();
  });

  it("falls back to slug/'Venue' when label lookup misses", () => {
    render(
      <ActiveFilterChips
        filters={{
          ...EMPTY_FILTERS,
          genres: ["mystery-genre"],
          venueIds: ["unknown-id"],
        }}
        baseParams={new URLSearchParams()}
        genreLabels={{}}
        venueLabels={{}}
      />,
    );
    expect(screen.getByText("Genre: mystery-genre")).toBeInTheDocument();
    expect(screen.getByText("Venue: Venue")).toBeInTheDocument();
  });

  it("uses 'Free shows only' instead of a price chip when freeOnly is true", () => {
    render(
      <ActiveFilterChips
        filters={{ ...EMPTY_FILTERS, freeOnly: true, priceMax: 50 }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.getByText("Free shows only")).toBeInTheDocument();
    expect(screen.queryByText(/Under/)).not.toBeInTheDocument();
  });

  it("each chip's href drops only its own dimension and preserves base params", () => {
    render(
      <ActiveFilterChips
        filters={{
          ...EMPTY_FILTERS,
          genres: ["indie"],
          artistSearch: "phoebe",
        }}
        baseParams={new URLSearchParams("city=washington-dc")}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    const genreHref = screen
      .getByText("Genre: Indie")
      .closest("a")
      ?.getAttribute("href");
    expect(genreHref).toContain("city=washington-dc");
    expect(genreHref).toContain("artist=phoebe");
    expect(genreHref).not.toContain("genre=");

    const artistHref = screen
      .getByText("Artist: phoebe")
      .closest("a")
      ?.getAttribute("href");
    expect(artistHref).toContain("genre=indie");
    expect(artistHref).not.toContain("artist=");
  });

  it("renders 'Clear all' only when 2+ chips are active", () => {
    const { rerender } = render(
      <ActiveFilterChips
        filters={{ ...EMPTY_FILTERS, genres: ["indie"] }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.queryByText("Clear all")).not.toBeInTheDocument();

    rerender(
      <ActiveFilterChips
        filters={{
          ...EMPTY_FILTERS,
          genres: ["indie"],
          availableOnly: true,
        }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.getByText("Clear all")).toBeInTheDocument();
  });

  it("Clear-all href drops every filter param but keeps base params", () => {
    render(
      <ActiveFilterChips
        filters={{
          ...EMPTY_FILTERS,
          genres: ["indie"],
          availableOnly: true,
          freeOnly: true,
        }}
        baseParams={new URLSearchParams("city=washington-dc&view=calendar")}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    const href = screen
      .getByText("Clear all")
      .closest("a")
      ?.getAttribute("href");
    expect(href).toContain("city=washington-dc");
    expect(href).toContain("view=calendar");
    expect(href).not.toContain("genre=");
    expect(href).not.toContain("free=");
    expect(href).not.toContain("available=");
  });

  it("renders a date-range chip when only one endpoint is set", () => {
    render(
      <ActiveFilterChips
        filters={{ ...EMPTY_FILTERS, dateFrom: "2026-05-01" }}
        baseParams={new URLSearchParams()}
        genreLabels={GENRES}
        venueLabels={VENUES}
      />,
    );
    expect(screen.getByText("Date: From 2026-05-01")).toBeInTheDocument();
  });
});
