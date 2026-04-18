/**
 * About page — `/about` (server-side rendered).
 *
 * Plain-content intro for first-time visitors and AI crawlers. Answers
 * "what is this site, how fresh is the data, who runs it" in one quick
 * scroll so the calendar feels trustworthy before a user decides to
 * browse deeper.
 */

import Link from "next/link";
import type { Metadata } from "next";

import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { absolutePageUrl, buildPageMetadata } from "@/lib/metadata";

export const revalidate = 3600;

export function generateMetadata(): Metadata {
  return buildPageMetadata({
    title: "About Greenroom — DMV Concert Calendar",
    description:
      "Greenroom is a DMV-wide concert calendar aggregating shows from every major DC, Maryland, and Virginia venue. Updated nightly.",
    path: "/about",
  });
}

export default function AboutPage(): JSX.Element {
  return (
    <>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "About", url: absolutePageUrl("/about") },
        ]}
      />

      <article className="flex flex-col gap-6 py-6">
        <header className="flex flex-col gap-2">
          <p className="text-sm font-semibold uppercase tracking-widest text-accent">
            About
          </p>
          <h1 className="text-3xl font-bold leading-tight sm:text-4xl">
            Every DMV concert in one place.
          </h1>
        </header>

        <section className="flex flex-col gap-3 text-base leading-relaxed text-foreground">
          <p>
            Greenroom aggregates upcoming concerts from every major venue
            across Washington DC, Maryland, and Virginia. It exists because
            no single ticketing platform covers the whole DMV — shows are
            scattered across Ticketmaster, DICE, venue websites, and
            independent promoters, which makes it hard to see what&apos;s
            actually happening on any given night.
          </p>
          <p>
            This site pulls them all together into one calendar so you can
            scan the next week, the next month, or a specific venue without
            hopping between tabs.
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-xl font-semibold">How fresh is the data?</h2>
          <p className="text-base text-foreground">
            Event listings refresh nightly from venue websites and
            Ticketmaster. Venues themselves — addresses, capacities,
            websites — are hand-curated from their public info.
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-xl font-semibold">Coming soon</h2>
          <p className="text-base text-foreground">
            Spotify-powered recommendations that score upcoming shows
            against your listening history. Sign-in and saved shows land
            when the Spotify integration is live.
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-xl font-semibold">Explore</h2>
          <ul className="flex flex-col gap-1 text-base">
            <li>
              <Link href="/events" className="text-accent hover:underline">
                Browse the full calendar →
              </Link>
            </li>
            <li>
              <Link href="/venues" className="text-accent hover:underline">
                Directory of DMV venues →
              </Link>
            </li>
            <li>
              <a
                href="/sitemap.xml"
                className="text-accent hover:underline"
              >
                Sitemap →
              </a>
            </li>
          </ul>
        </section>
      </article>
    </>
  );
}
