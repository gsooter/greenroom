/**
 * Multi-source pricing panel — client component.
 *
 * Renders one row per provider with the current min/max price, a Buy
 * CTA (preferring the affiliate URL when present), and a "Refresh"
 * button that POSTs to the manual-refresh endpoint. The button is
 * gated by a 5-minute cooldown shared across every visitor (DB-backed
 * on the backend), so refreshing in one tab cools the button down on
 * every other open tab.
 *
 * The server hands us the initial pricing state — this component only
 * re-fetches in response to the user pressing the button.
 */

"use client";

import { useCallback, useState, useTransition } from "react";

import { refreshEventPricing } from "@/lib/api/events";
import { formatPriceRange, formatRelativeTime } from "@/lib/format";
import type { PricingSource, PricingState } from "@/types";

const PROVIDER_LABELS: Record<string, string> = {
  seatgeek: "SeatGeek",
  ticketmaster: "Ticketmaster",
  tickpick: "TickPick",
  dice: "DICE",
  eventbrite: "Eventbrite",
  etix: "Etix",
  axs: "AXS",
  see_tickets: "See Tickets",
  generic_html: "Venue site",
  black_cat: "Black Cat",
  comet_ping_pong: "Comet Ping Pong",
  pie_shop: "Pie Shop",
  the_hamilton: "The Hamilton",
  the_camel: "The Camel",
  ottobar: "Ottobar",
  bethesda_theater: "Bethesda Theater",
  pearl_street_warehouse: "Pearl Street Warehouse",
};

function providerLabel(source: string): string {
  return PROVIDER_LABELS[source] ?? source.replace(/_/g, " ");
}

interface EventPricingPanelProps {
  eventIdOrSlug: string;
  initial: PricingState;
}

export default function EventPricingPanel({
  eventIdOrSlug,
  initial,
}: EventPricingPanelProps): JSX.Element {
  const [pricing, setPricing] = useState<PricingState>(initial);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState<boolean>(false);
  const [isPending, startTransition] = useTransition();

  const handleRefresh = useCallback((): void => {
    setError(null);
    setCooldown(false);
    startTransition(() => {
      void (async () => {
        try {
          const res = await refreshEventPricing(eventIdOrSlug);
          setPricing(res.pricing);
          setCooldown(res.refresh.cooldown_active);
        } catch (err) {
          const msg = err instanceof Error ? err.message : "Refresh failed";
          setError(msg);
        }
      })();
    });
  }, [eventIdOrSlug]);

  const sources = pricing.sources;
  const hasSources = sources.length > 0;

  return (
    <section
      aria-label="Ticket pricing across providers"
      className="flex flex-col gap-4 rounded-lg border border-border bg-bg-surface/60 p-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col">
          <h2 className="text-base font-semibold text-text-primary">
            Compare ticket prices
          </h2>
          <p className="text-xs text-text-secondary">
            Updated {formatRelativeTime(pricing.refreshed_at)}
          </p>
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={isPending}
          aria-label="Refresh ticket prices"
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-white px-3 py-1.5 text-xs font-semibold text-text-primary transition hover:border-green-primary hover:text-green-primary disabled:opacity-60"
        >
          <RefreshIcon spinning={isPending} />
          {isPending ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {cooldown ? (
        <p
          role="status"
          className="rounded-md border border-border bg-bg-base/60 px-3 py-2 text-xs text-text-secondary"
        >
          These prices were just refreshed. Try again in a few minutes.
        </p>
      ) : null}

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-blush-accent bg-blush-soft px-3 py-2 text-xs text-blush-accent"
        >
          {error}
        </p>
      ) : null}

      {hasSources ? (
        <ul className="flex flex-col gap-2">
          {sources.map((source) => (
            <PricingRow key={source.source} source={source} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-secondary">
          No ticket sources have been priced for this show yet.
        </p>
      )}
    </section>
  );
}

function PricingRow({ source }: { source: PricingSource }): JSX.Element {
  const priceLabel = formatPriceRange(source.min_price, source.max_price);
  const buyUrl = source.affiliate_url ?? source.buy_url;
  const inactive = !source.is_active;

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-bg-white px-3 py-2">
      <div className="flex min-w-0 flex-col">
        <span className="text-sm font-semibold text-text-primary">
          {providerLabel(source.source)}
        </span>
        <span className="text-xs text-text-secondary">
          {priceLabel ?? "Price unavailable"}
          {source.listing_count != null ? (
            <>
              {" "}· {source.listing_count} listing
              {source.listing_count === 1 ? "" : "s"}
            </>
          ) : null}
          {inactive ? <> · sold out</> : null}
        </span>
      </div>
      {buyUrl ? (
        <a
          href={buyUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={
            "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-xs font-semibold transition " +
            (inactive
              ? "border border-border bg-bg-surface text-text-secondary hover:border-green-primary hover:text-green-primary"
              : "bg-green-primary text-text-inverse hover:opacity-90")
          }
        >
          {inactive ? "View" : "Buy"}
        </a>
      ) : null}
    </li>
  );
}

function RefreshIcon({ spinning }: { spinning: boolean }): JSX.Element {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={14}
      height={14}
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={spinning ? "animate-spin" : undefined}
    >
      <path d="M21 12a9 9 0 1 1-3-6.7" />
      <path d="M21 4v5h-5" />
    </svg>
  );
}
