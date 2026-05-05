/**
 * Bottom-of-section CTA for the home page's "Browse all DMV shows" grid.
 *
 * The header version of this link ("See all →") is a small text-only
 * affordance that's easy to miss after scrolling through eight cards on
 * mobile. This component renders a high-contrast, full-width button on
 * narrow viewports (and a right-aligned inline button at sm: and up) so
 * users always have an obvious "show me everything" exit at the end of
 * the section.
 */

import Link from "next/link";

interface BrowseFooterCtaProps {
  href?: string;
  label?: string;
}

export default function BrowseFooterCta({
  href = "/events",
  label = "View all DMV events",
}: BrowseFooterCtaProps): JSX.Element {
  return (
    <div className="flex justify-center pt-4 sm:justify-end">
      <Link
        href={href}
        data-testid="home-browse-footer-cta"
        className="inline-flex w-full items-center justify-center rounded-md border border-border bg-bg-white px-5 py-3 text-sm font-semibold text-foreground transition hover:border-accent hover:text-accent sm:w-auto"
      >
        {label} →
      </Link>
    </div>
  );
}
