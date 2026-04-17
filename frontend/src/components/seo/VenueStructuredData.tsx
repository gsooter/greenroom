/**
 * Renders MusicVenue JSON-LD structured data for venue detail pages.
 *
 * Required by CLAUDE.md on every venue page. Provides Google and AI
 * crawlers with address, capacity, and the venue's canonical URL.
 */

import type { VenueDetail } from "@/types";

interface VenueStructuredDataProps {
  venue: VenueDetail;
  canonicalUrl: string;
}

export default function VenueStructuredData({
  venue,
  canonicalUrl,
}: VenueStructuredDataProps) {
  const payload = {
    "@context": "https://schema.org",
    "@type": "MusicVenue",
    name: venue.name,
    url: canonicalUrl,
    description: venue.description ?? undefined,
    image: venue.image_url ?? undefined,
    maximumAttendeeCapacity: venue.capacity ?? undefined,
    sameAs: venue.website_url ?? undefined,
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
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
