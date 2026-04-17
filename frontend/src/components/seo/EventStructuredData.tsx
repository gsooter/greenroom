/**
 * Renders MusicEvent JSON-LD structured data for event detail pages.
 *
 * Required by CLAUDE.md on every event page. Drives Google rich
 * results for event searches and gives AI crawlers a canonical
 * representation of each show. Fields are built directly from the
 * backend `EventDetail` + `VenueDetail` payloads.
 */

import type { EventDetail, EventStatus, VenueDetail } from "@/types";

interface EventStructuredDataProps {
  event: EventDetail;
  venue: VenueDetail;
  canonicalUrl: string;
}

const STATUS_SCHEMA: Record<EventStatus, string> = {
  announced: "https://schema.org/EventScheduled",
  on_sale: "https://schema.org/EventScheduled",
  confirmed: "https://schema.org/EventScheduled",
  sold_out: "https://schema.org/EventScheduled",
  cancelled: "https://schema.org/EventCancelled",
  postponed: "https://schema.org/EventPostponed",
};

const AVAILABILITY_SCHEMA: Partial<Record<EventStatus, string>> = {
  on_sale: "https://schema.org/InStock",
  confirmed: "https://schema.org/InStock",
  sold_out: "https://schema.org/SoldOut",
};

export default function EventStructuredData({
  event,
  venue,
  canonicalUrl,
}: EventStructuredDataProps) {
  const performers = event.artists.map((name) => ({
    "@type": "MusicGroup",
    name,
  }));

  const offers =
    event.ticket_url && event.min_price != null
      ? {
          "@type": "Offer",
          url: event.ticket_url,
          price: event.min_price,
          priceCurrency: "USD",
          availability:
            AVAILABILITY_SCHEMA[event.status] ?? "https://schema.org/InStock",
          validFrom: event.created_at,
        }
      : event.ticket_url
        ? {
            "@type": "Offer",
            url: event.ticket_url,
            availability:
              AVAILABILITY_SCHEMA[event.status] ?? "https://schema.org/InStock",
          }
        : undefined;

  const payload = {
    "@context": "https://schema.org",
    "@type": "MusicEvent",
    name: event.title,
    url: canonicalUrl,
    startDate: event.starts_at,
    endDate: event.ends_at,
    doorTime: event.doors_at,
    eventStatus: STATUS_SCHEMA[event.status],
    eventAttendanceMode: "https://schema.org/OfflineEventAttendanceMode",
    description: event.description ?? undefined,
    image: event.image_url ? [event.image_url] : undefined,
    performer: performers.length > 0 ? performers : undefined,
    offers,
    location: {
      "@type": "MusicVenue",
      name: venue.name,
      address: venue.address
        ? {
            "@type": "PostalAddress",
            streetAddress: venue.address,
            addressLocality: venue.city?.name,
            addressRegion: venue.city?.state,
            addressCountry: "US",
          }
        : undefined,
      geo:
        venue.latitude != null && venue.longitude != null
          ? {
              "@type": "GeoCoordinates",
              latitude: venue.latitude,
              longitude: venue.longitude,
            }
          : undefined,
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
