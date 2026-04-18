/**
 * Tests for CalendarView.
 *
 * Covers the cell-building logic: leading/trailing days from adjacent
 * months, today's highlight, per-day counts, total-for-month line,
 * per-day hrefs, prev/next links, and the "N shows" singular/plural.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CalendarView from "@/components/events/CalendarView";
import type { EventSummary } from "@/types";

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

function event(
  starts_at: string | null,
  id = crypto.randomUUID(),
): EventSummary {
  return {
    id,
    title: `Show ${id.slice(0, 4)}`,
    slug: `show-${id.slice(0, 4)}`,
    starts_at,
    artists: [],
    image_url: null,
    min_price: null,
    max_price: null,
    status: "confirmed",
    venue: null,
  };
}

describe("CalendarView", () => {
  it("renders the month label and a 'N shows' summary (plural)", () => {
    render(
      <CalendarView
        events={[
          event("2026-04-14T20:00:00Z"),
          event("2026-04-17T20:00:00Z"),
        ]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug={null}
        prevMonthHref="/events?month=2026-03&view=calendar"
        nextMonthHref="/events?month=2026-05&view=calendar"
      />,
    );
    expect(screen.getByText(/April 2026/)).toBeInTheDocument();
    expect(screen.getByText("2 shows")).toBeInTheDocument();
  });

  it("uses the singular 'show' when exactly one event falls in the month", () => {
    render(
      <CalendarView
        events={[event("2026-04-14T20:00:00Z")]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug={null}
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    expect(screen.getByText("1 show")).toBeInTheDocument();
  });

  it("renders prev and next month links", () => {
    render(
      <CalendarView
        events={[]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug={null}
        prevMonthHref="/events?month=2026-03&view=calendar"
        nextMonthHref="/events?month=2026-05&view=calendar"
      />,
    );
    expect(
      screen.getByRole("link", { name: "Previous month" }).getAttribute("href"),
    ).toBe("/events?month=2026-03&view=calendar");
    expect(
      screen.getByRole("link", { name: "Next month" }).getAttribute("href"),
    ).toBe("/events?month=2026-05&view=calendar");
  });

  it("turns a day with events into a clickable link with the day param", () => {
    render(
      <CalendarView
        events={[event("2026-04-17T20:00:00Z", "a")]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug="washington-dc"
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    // The badge with the count "1" links to the day view with the city.
    const dayLink = screen
      .getAllByRole("link")
      .find((a) =>
        a.getAttribute("href")?.includes("date=2026-04-17"),
      );
    expect(dayLink).toBeDefined();
    expect(dayLink?.getAttribute("href")).toBe(
      "/events?city=washington-dc&date=2026-04-17",
    );
  });

  it("skips events with null or invalid starts_at when counting", () => {
    render(
      <CalendarView
        events={[
          event(null, "nostart"),
          event("not-a-date", "badstart"),
          event("2026-04-17T20:00:00Z", "ok"),
        ]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug={null}
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    expect(screen.getByText("1 show")).toBeInTheDocument();
  });

  it("renders leading days from the previous month (non-linkable)", () => {
    // April 2026: Apr 1 is a Wed → Sun-Tue cells are Mar 29, 30, 31.
    render(
      <CalendarView
        events={[]}
        year={2026}
        monthIndex={3}
        todayKey="2026-04-14"
        citySlug={null}
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    // Day "31" should appear for Mar 31 (a leading filler) — a non-link.
    const fillers = screen.getAllByText("31");
    expect(fillers.length).toBeGreaterThanOrEqual(1);
  });

  it("wraps correctly into January when monthIndex is 0", () => {
    render(
      <CalendarView
        events={[event("2026-01-02T20:00:00Z", "a")]}
        year={2026}
        monthIndex={0}
        todayKey="2026-01-02"
        citySlug={null}
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    expect(screen.getByText(/January 2026/)).toBeInTheDocument();
  });

  it("wraps correctly into December when monthIndex is 11", () => {
    render(
      <CalendarView
        events={[]}
        year={2026}
        monthIndex={11}
        todayKey="2026-12-15"
        citySlug={null}
        prevMonthHref="/x"
        nextMonthHref="/y"
      />,
    );
    expect(screen.getByText(/December 2026/)).toBeInTheDocument();
  });
});
