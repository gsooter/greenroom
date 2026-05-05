/**
 * Multi-source pricing panel — pure server component.
 *
 * Renders one row per provider with the current price (when known)
 * and a Buy CTA, preferring the affiliate URL over the raw buy URL.
 * The manual-refresh button was removed once we discovered most upstream
 * APIs withhold prices on the free tier — surfacing "Price unavailable"
 * and a refresh button that wouldn't change anything was confusing
 * users. The page just shows what we have, when we have it.
 */

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
  initial: PricingState;
}

export default function EventPricingPanel({
  initial,
}: EventPricingPanelProps): JSX.Element | null {
  const sources = initial.sources;
  if (sources.length === 0) {
    return null;
  }

  return (
    <section
      id="tickets"
      aria-label="Ticket sources"
      className="flex scroll-mt-24 flex-col gap-4 rounded-lg border border-border bg-bg-surface/60 p-4"
    >
      <header className="flex flex-col">
        <h2 className="text-base font-semibold text-text-primary">
          Get tickets
        </h2>
        <p className="text-xs text-text-secondary">
          Updated {formatRelativeTime(initial.refreshed_at)}
        </p>
      </header>

      <ul className="flex flex-col gap-2">
        {sources.map((source) => (
          <PricingRow key={source.source} source={source} />
        ))}
      </ul>
    </section>
  );
}

function PricingRow({ source }: { source: PricingSource }): JSX.Element {
  const priceLabel = formatPriceRange(source.min_price, source.max_price);
  const buyUrl = source.affiliate_url ?? source.buy_url;
  const inactive = !source.is_active;
  const meta = [
    priceLabel,
    source.listing_count != null
      ? `${source.listing_count} listing${source.listing_count === 1 ? "" : "s"}`
      : null,
    inactive ? "sold out" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-bg-white px-3 py-2">
      <div className="flex min-w-0 flex-col">
        <span className="text-sm font-semibold text-text-primary">
          {providerLabel(source.source)}
        </span>
        {meta ? (
          <span className="text-xs text-text-secondary">{meta}</span>
        ) : null}
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
