/**
 * Tests for BrowseFooterCta — the bottom-of-section "View all" link.
 *
 * Fix #5: the only existing "See all" affordance lived in the section
 * header and was a small text link that mobile users routinely missed
 * after scrolling through the cards. The footer CTA must be rendered as
 * a full-width button on mobile and point at /events by default.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import BrowseFooterCta from "@/components/home/BrowseFooterCta";

describe("BrowseFooterCta", () => {
  it("renders a link to /events by default", () => {
    render(<BrowseFooterCta />);
    const link = screen.getByTestId("home-browse-footer-cta");
    expect(link).toHaveAttribute("href", "/events");
    expect(link.textContent).toMatch(/view all dmv events/i);
  });

  it("uses full-width-on-mobile sizing classes for tap-friendly touch targets", () => {
    render(<BrowseFooterCta />);
    const link = screen.getByTestId("home-browse-footer-cta");
    // On mobile (default) the CTA must stretch the row; sm: and up it
    // collapses to its content width so it sits inline with the card grid.
    expect(link.className).toMatch(/\bw-full\b/);
    expect(link.className).toMatch(/sm:w-auto/);
  });

  it("accepts overrides for href and label", () => {
    render(<BrowseFooterCta href="/events?window=tonight" label="Browse tonight" />);
    const link = screen.getByTestId("home-browse-footer-cta");
    expect(link).toHaveAttribute("href", "/events?window=tonight");
    expect(link.textContent).toMatch(/browse tonight/i);
  });
});
