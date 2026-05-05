/**
 * Tests for EventPricingPanel.
 *
 * The panel is a pure render of the multi-source pricing list — the
 * manual-refresh button was removed when most upstream APIs proved
 * unable to return prices on our tier. These tests cover provider
 * label rendering, price/listing-count formatting, the affiliate-URL
 * preference, sold-out behavior, and the empty-state suppression.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import EventPricingPanel from "@/components/events/EventPricingPanel";
import type { PricingSource, PricingState } from "@/types";

function source(overrides: Partial<PricingSource> = {}): PricingSource {
  return {
    source: "seatgeek",
    buy_url: "https://buy.example/sg",
    affiliate_url: null,
    is_active: true,
    currency: "USD",
    min_price: 30,
    max_price: 80,
    average_price: null,
    listing_count: 12,
    last_seen_at: null,
    last_active_at: null,
    ...overrides,
  };
}

function state(overrides: Partial<PricingState> = {}): PricingState {
  return {
    refreshed_at: "2026-04-26T11:30:00.000Z",
    sources: [source()],
    ...overrides,
  };
}

describe("EventPricingPanel", () => {
  it("renders the provider label, price range, and listing count", () => {
    render(<EventPricingPanel initial={state()} />);

    expect(screen.getByText("SeatGeek")).toBeInTheDocument();
    expect(screen.getByText(/\$30–\$80/)).toBeInTheDocument();
    expect(screen.getByText(/12 listings/)).toBeInTheDocument();
  });

  it("renders nothing when there are no sources", () => {
    const { container } = render(
      <EventPricingPanel initial={state({ sources: [] })} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("omits the metadata line when there is no price, count, or sold-out signal", () => {
    render(
      <EventPricingPanel
        initial={state({
          sources: [
            source({
              min_price: null,
              max_price: null,
              listing_count: null,
              is_active: true,
            }),
          ],
        })}
      />,
    );

    expect(screen.queryByText(/Price unavailable/i)).not.toBeInTheDocument();
    expect(screen.getByText("SeatGeek")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /buy/i })).toBeInTheDocument();
  });

  it("prefers the affiliate URL over the raw buy URL", () => {
    render(
      <EventPricingPanel
        initial={state({
          sources: [
            source({
              buy_url: "https://buy.example/raw",
              affiliate_url: "https://aff.example/x",
            }),
          ],
        })}
      />,
    );

    const cta = screen.getByRole("link", { name: /buy/i });
    expect(cta.getAttribute("href")).toBe("https://aff.example/x");
  });

  it("labels inactive (sold-out) rows with 'View' instead of 'Buy'", () => {
    render(
      <EventPricingPanel
        initial={state({
          sources: [source({ is_active: false })],
        })}
      />,
    );

    expect(screen.getByText(/sold out/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view/i })).toBeInTheDocument();
  });

  it("does not render a refresh button", () => {
    render(<EventPricingPanel initial={state()} />);
    expect(
      screen.queryByRole("button", { name: /refresh/i }),
    ).not.toBeInTheDocument();
  });

  it("exposes id='tickets' on the panel so the top CTA can anchor-scroll to it", () => {
    const { container } = render(<EventPricingPanel initial={state()} />);
    const section = container.querySelector("section#tickets");
    expect(section).not.toBeNull();
    // Anchored sections that scroll into view should leave a little
    // breathing room under any sticky/transparent top nav.
    expect(section?.className).toMatch(/scroll-mt-/);
  });

  it("falls back to a humanized source slug when no label is mapped", () => {
    render(
      <EventPricingPanel
        initial={state({
          sources: [source({ source: "made_up_provider" })],
        })}
      />,
    );

    expect(screen.getByText(/made up provider/)).toBeInTheDocument();
  });
});
