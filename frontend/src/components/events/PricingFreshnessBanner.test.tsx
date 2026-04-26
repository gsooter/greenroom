/**
 * Tests for PricingFreshnessBanner.
 *
 * The banner is a thin shell over `formatRelativeTime` — these
 * assertions cover the routing of the timestamp through that helper
 * with an injected `now` for determinism.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PricingFreshnessBanner from "@/components/events/PricingFreshnessBanner";

const NOW = new Date("2026-04-26T12:00:00Z");

describe("PricingFreshnessBanner", () => {
  it("renders 'never' when no sweep has run yet", () => {
    render(<PricingFreshnessBanner refreshedAt={null} now={NOW} />);
    expect(screen.getByText(/Last sweep never/)).toBeInTheDocument();
  });

  it("renders a coarse relative label for a recent sweep", () => {
    const recent = new Date(NOW.getTime() - 5 * 60_000).toISOString();
    render(<PricingFreshnessBanner refreshedAt={recent} now={NOW} />);
    expect(screen.getByText(/5 minutes ago/)).toBeInTheDocument();
  });

  it("falls back to a long date past one week", () => {
    const old = new Date(NOW.getTime() - 9 * 24 * 60 * 60_000).toISOString();
    render(<PricingFreshnessBanner refreshedAt={old} now={NOW} />);
    expect(screen.getByLabelText(/freshness/i)).toHaveTextContent(/2026/);
  });
});
