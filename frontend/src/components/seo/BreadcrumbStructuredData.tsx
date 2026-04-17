/**
 * Renders BreadcrumbList JSON-LD structured data.
 *
 * Required by CLAUDE.md on every public page. Each crumb is a
 * `{name, url}` pair; URLs should be fully qualified so crawlers can
 * follow them regardless of page context.
 */

interface Crumb {
  name: string;
  url: string;
}

interface BreadcrumbStructuredDataProps {
  items: Crumb[];
}

export default function BreadcrumbStructuredData({
  items,
}: BreadcrumbStructuredDataProps) {
  const payload = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: item.url,
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
