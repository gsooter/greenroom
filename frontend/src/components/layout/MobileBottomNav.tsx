/**
 * Mobile bottom navigation bar — client component.
 *
 * Rendered only below the `sm` breakpoint. Highlights the active route
 * by checking the current pathname so the user always knows which
 * section of the app they're in.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { config } from "@/lib/config";

interface NavItem {
  href: string;
  label: string;
}

const BASE_ITEMS: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/events", label: "Events" },
  { href: "/venues", label: "Venues" },
];

const ITEMS: NavItem[] = config.spotifyLoginEnabled
  ? [...BASE_ITEMS, { href: "/login", label: "Sign in" }]
  : [...BASE_ITEMS, { href: "/about", label: "About" }];

export default function MobileBottomNav() {
  const pathname = usePathname();
  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 backdrop-blur sm:hidden">
      <ul className="mx-auto grid max-w-6xl grid-cols-4">
        {ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={
                  "flex items-center justify-center px-2 py-3 text-xs font-medium " +
                  (active ? "text-accent" : "text-muted hover:text-foreground")
                }
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
