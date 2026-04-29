/**
 * Listing-page freshness banner — pure server component.
 *
 * Surfaces the most recent pricing-sweep timestamp so visitors know
 * how stale the prices on the cards are. The data lives on the
 * `prices_refreshed_at` column of every event; this component only
 * renders the global max — per-card freshness would be too noisy.
 *
 * `now` is injectable so SSR is deterministic and tests don't need
 * fake timers.
 */

import { formatRelativeTime } from "@/lib/format";

interface PricingFreshnessBannerProps {
  refreshedAt: string | null;
  now?: Date;
}

export default function PricingFreshnessBanner({
  refreshedAt,
  now,
}: PricingFreshnessBannerProps): JSX.Element {
  const label = formatRelativeTime(refreshedAt, now);
  return (
    <p
      className="text-xs text-text-secondary"
      aria-label="Ticket pricing freshness"
    >
      Ticket prices refresh nightly · Last sweep {label}
    </p>
  );
}
