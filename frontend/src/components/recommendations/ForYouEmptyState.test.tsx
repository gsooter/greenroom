/**
 * Tests for ForYouEmptyState.
 *
 * The two variants exist because they suggest different next steps —
 * "connect Spotify" only makes sense when the user actually has nothing
 * connected. These tests guard against the variants getting swapped.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ForYouEmptyState from "@/components/recommendations/ForYouEmptyState";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

describe("ForYouEmptyState", () => {
  it("for no_signal points the user at /settings to connect", () => {
    render(<ForYouEmptyState variant="no_signal" />);
    expect(
      screen.getByRole("heading", {
        name: /connect a music service to see picks/i,
      }),
    ).toBeInTheDocument();
    const settingsLink = screen.getByRole("link", { name: /settings/i });
    expect(settingsLink).toHaveAttribute("href", "/settings");
  });

  it("for no_matches points the user at /events to keep browsing", () => {
    render(<ForYouEmptyState variant="no_matches" />);
    expect(
      screen.getByRole("heading", { name: /no matches yet/i }),
    ).toBeInTheDocument();
    const calendarLink = screen.getByRole("link", { name: /full calendar/i });
    expect(calendarLink).toHaveAttribute("href", "/events");
  });

  it("does not surface the connect-a-service prompt when the user already has signal", () => {
    render(<ForYouEmptyState variant="no_matches" />);
    expect(
      screen.queryByRole("heading", { name: /connect a music service/i }),
    ).toBeNull();
  });
});
