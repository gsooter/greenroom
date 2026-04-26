/**
 * Tests for EventPricingPanel.
 *
 * The panel is a small client component that renders the multi-source
 * pricing list, owns a Refresh button gated by a 5-minute backend
 * cooldown, and prefers affiliate URLs over raw buy URLs. We mock the
 * API client so the assertions stay focused on rendering, error
 * routing, and the cooldown banner.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EventPricingPanel from "@/components/events/EventPricingPanel";
import type { PricingSource, PricingState, RefreshPricingResponse } from "@/types";

const refreshEventPricing = vi.fn<
  (idOrSlug: string) => Promise<RefreshPricingResponse>
>();

vi.mock("@/lib/api/events", () => ({
  refreshEventPricing: (idOrSlug: string) => refreshEventPricing(idOrSlug),
}));

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
  beforeEach(() => {
    refreshEventPricing.mockReset();
  });

  it("renders the provider label, price range, and listing count", () => {
    render(
      <EventPricingPanel eventIdOrSlug="some-show" initial={state()} />,
    );

    expect(screen.getByText("SeatGeek")).toBeInTheDocument();
    expect(screen.getByText(/\$30–\$80/)).toBeInTheDocument();
    expect(screen.getByText(/12 listings/)).toBeInTheDocument();
  });

  it("shows a 'never' freshness label when refreshed_at is null", () => {
    render(
      <EventPricingPanel
        eventIdOrSlug="some-show"
        initial={state({ refreshed_at: null })}
      />,
    );

    expect(screen.getByText(/Updated never/)).toBeInTheDocument();
  });

  it("renders the empty-state message when there are no sources", () => {
    render(
      <EventPricingPanel
        eventIdOrSlug="some-show"
        initial={state({ sources: [] })}
      />,
    );

    expect(
      screen.getByText(/No ticket sources have been priced/i),
    ).toBeInTheDocument();
  });

  it("prefers the affiliate URL over the raw buy URL", () => {
    render(
      <EventPricingPanel
        eventIdOrSlug="some-show"
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
        eventIdOrSlug="some-show"
        initial={state({
          sources: [source({ is_active: false })],
        })}
      />,
    );

    expect(screen.getByText(/sold out/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view/i })).toBeInTheDocument();
  });

  it("calls refreshEventPricing and merges the new pricing on click", async () => {
    refreshEventPricing.mockResolvedValueOnce({
      refresh: {
        event_id: "evt-1",
        refreshed_at: "2026-04-26T12:00:00.000Z",
        cooldown_active: false,
        quotes_persisted: 1,
        links_upserted: 1,
        provider_errors: [],
      },
      pricing: state({
        refreshed_at: "2026-04-26T12:00:00.000Z",
        sources: [
          source({ source: "ticketmaster", min_price: 50, max_price: 50 }),
        ],
      }),
    });

    render(
      <EventPricingPanel eventIdOrSlug="some-show" initial={state()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(screen.getByText("Ticketmaster")).toBeInTheDocument();
    });
    expect(refreshEventPricing).toHaveBeenCalledWith("some-show");
  });

  it("renders the cooldown banner when the backend reports cooldown_active", async () => {
    refreshEventPricing.mockResolvedValueOnce({
      refresh: {
        event_id: "evt-1",
        refreshed_at: "2026-04-26T11:30:00.000Z",
        cooldown_active: true,
        quotes_persisted: 0,
        links_upserted: 0,
        provider_errors: [],
      },
      pricing: state(),
    });

    render(
      <EventPricingPanel eventIdOrSlug="some-show" initial={state()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        /just refreshed/i,
      );
    });
  });

  it("surfaces an error message when the refresh call rejects", async () => {
    refreshEventPricing.mockRejectedValueOnce(new Error("boom"));

    render(
      <EventPricingPanel eventIdOrSlug="some-show" initial={state()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("boom");
    });
  });

  it("falls back to a humanized source slug when no label is mapped", () => {
    render(
      <EventPricingPanel
        eventIdOrSlug="some-show"
        initial={state({
          sources: [source({ source: "made_up_provider" })],
        })}
      />,
    );

    expect(screen.getByText(/made up provider/)).toBeInTheDocument();
  });
});
