/**
 * Top navigation bar — server component.
 *
 * Fetches the DMV city list at render time so every page gets a
 * consistent picker without a client round-trip. If the backend is
 * unavailable, the picker is omitted but navigation links still render.
 */

import Link from "next/link";
import { Suspense } from "react";

import AuthNav from "@/components/layout/AuthNav";
import CityPicker from "@/components/layout/CityPicker";
import { listCities } from "@/lib/api/cities";
import type { City } from "@/types";

interface TopNavProps {
  selectedCitySlug: string | null;
}

export default async function TopNav({ selectedCitySlug }: TopNavProps) {
  let cities: City[] = [];
  try {
    cities = await listCities({ region: "DMV", revalidateSeconds: 300 });
  } catch {
    cities = [];
  }

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 text-lg font-semibold tracking-tight text-foreground hover:text-accent"
        >
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-full bg-accent"
          />
          Greenroom
        </Link>

        <nav className="hidden items-center gap-1 text-sm sm:flex">
          <NavLink href="/events">Events</NavLink>
          <NavLink href="/venues">Venues</NavLink>
        </nav>

        <div className="flex items-center gap-3">
          {cities.length > 0 ? (
            <Suspense fallback={null}>
              <CityPicker cities={cities} selectedSlug={selectedCitySlug} />
            </Suspense>
          ) : null}
          <AuthNav />
        </div>
      </div>
    </header>
  );
}

function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-surface hover:text-foreground"
    >
      {children}
    </Link>
  );
}
