/**
 * Shows Near Me — ``/near-me`` (server-side rendered shell).
 *
 * The interactive surface is the {@link NearMeShell} client component,
 * which owns geolocation, filters, and the map/list toggle. This page
 * only provides metadata, breadcrumb structured data, and the shell
 * container copy — no data is fetched on the server because the user's
 * location isn't available until the client asks for it.
 */

import type { Metadata } from "next";

import NearMeShell from "@/components/map/NearMeShell";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { absolutePageUrl, buildPageMetadata } from "@/lib/metadata";

export function generateMetadata(): Metadata {
  return buildPageMetadata({
    title: "Shows Near Me — Greenroom",
    description:
      "Find DMV concerts happening near you. Share your location and see nearby shows on a map or list, sorted nearest-first, for tonight or this week.",
    path: "/near-me",
  });
}

export default function NearMePage(): JSX.Element {
  return (
    <>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Near Me", url: absolutePageUrl("/near-me") },
        ]}
      />

      <section className="flex flex-col gap-4 pb-6 pt-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold sm:text-3xl">Shows near you</h1>
          <p className="text-sm text-muted">
            Share your location and we&apos;ll pull every DMV concert
            happening around you, sorted nearest-first.
          </p>
        </div>
      </section>

      <NearMeShell />
    </>
  );
}
