/**
 * Tests for the date-window filter chip row on /events.
 *
 * The chips are just `<Link>`s that rewrite the `window` query param.
 * Verify href composition (with and without an active city) and the
 * "click the active chip to clear it" toggle behavior.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import WindowFilterChips from "@/components/events/WindowFilterChips";

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

describe("WindowFilterChips", () => {
  it("renders all three options", () => {
    render(<WindowFilterChips active={null} citySlug={null} />);
    expect(screen.getByText("Tonight")).toBeInTheDocument();
    expect(screen.getByText("This weekend")).toBeInTheDocument();
    expect(screen.getByText("Next 7 days")).toBeInTheDocument();
  });

  it("builds hrefs with the window param when inactive", () => {
    render(<WindowFilterChips active={null} citySlug={null} />);
    expect(screen.getByText("Tonight").closest("a")?.getAttribute("href")).toBe(
      "/events?window=tonight",
    );
    expect(
      screen.getByText("This weekend").closest("a")?.getAttribute("href"),
    ).toBe("/events?window=weekend");
  });

  it("includes the city param when present", () => {
    render(<WindowFilterChips active={null} citySlug="washington-dc" />);
    expect(screen.getByText("Tonight").closest("a")?.getAttribute("href")).toBe(
      "/events?city=washington-dc&window=tonight",
    );
  });

  it("drops the window param on the active chip (toggle-to-clear)", () => {
    render(
      <WindowFilterChips active="weekend" citySlug="washington-dc" />,
    );
    // Active chip's href clears window:
    expect(
      screen.getByText("This weekend").closest("a")?.getAttribute("href"),
    ).toBe("/events?city=washington-dc");
    // Inactive chips still set their own window:
    expect(screen.getByText("Tonight").closest("a")?.getAttribute("href")).toBe(
      "/events?city=washington-dc&window=tonight",
    );
  });

  it("marks the active chip with aria-pressed=true", () => {
    render(<WindowFilterChips active="tonight" citySlug={null} />);
    expect(
      screen.getByText("Tonight").closest("a")?.getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByText("This weekend").closest("a")?.getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("returns bare /events when nothing is active and no city", () => {
    render(<WindowFilterChips active="tonight" citySlug={null} />);
    expect(screen.getByText("Tonight").closest("a")?.getAttribute("href")).toBe(
      "/events",
    );
  });
});
