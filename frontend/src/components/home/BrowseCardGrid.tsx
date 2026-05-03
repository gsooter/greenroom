/**
 * Client wrapper around the home page's "Browse all DMV shows" grid.
 *
 * The events themselves are SSR-fetched in ``app/page.tsx`` and passed
 * in as a prop so anonymous and crawler-served renders keep their
 * fully-populated HTML. This component only owns the layout switch:
 * it reads :func:`useCompactMode` and re-renders the cards in either
 * the comfortable grid or a single-column compact list whenever the
 * preference changes.
 */

"use client";

import EventCard from "@/components/events/EventCard";
import { useCompactMode } from "@/lib/home-preferences";
import type { EventSummary } from "@/types";

interface BrowseCardGridProps {
  events: EventSummary[];
}

export default function BrowseCardGrid({ events }: BrowseCardGridProps): JSX.Element {
  const [compact] = useCompactMode();

  if (compact) {
    return (
      <ul
        className="flex flex-col gap-2"
        data-testid="home-browse-grid"
        data-compact="true"
      >
        {events.map((event) => (
          <li key={event.id}>
            <EventCard event={event} compact />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      data-testid="home-browse-grid"
      data-compact="false"
    >
      {events.map((event) => (
        <EventCard key={event.id} event={event} />
      ))}
    </div>
  );
}
