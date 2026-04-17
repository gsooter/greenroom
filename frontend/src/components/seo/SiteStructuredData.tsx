/**
 * Site-level JSON-LD for the home page.
 *
 * Emits both `WebSite` and `Organization` schemas so crawlers can
 * associate the brand with its canonical URL. Rendered only on `/`.
 */

import { config } from "@/lib/config";

export default function SiteStructuredData() {
  const base = config.baseUrl.replace(/\/$/, "");
  const payload = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${base}/#website`,
        name: "Greenroom",
        url: `${base}/`,
        description:
          "The DMV's concert calendar with Spotify-powered recommendations.",
        potentialAction: {
          "@type": "SearchAction",
          target: `${base}/events?q={search_term_string}`,
          "query-input": "required name=search_term_string",
        },
      },
      {
        "@type": "Organization",
        "@id": `${base}/#organization`,
        name: "Greenroom",
        url: `${base}/`,
        areaServed: "Washington DC Metropolitan Area",
      },
    ],
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
