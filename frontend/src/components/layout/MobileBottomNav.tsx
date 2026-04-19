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

import { useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
}

const BASE_ITEMS: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/events", label: "Events" },
  { href: "/venues", label: "Venues" },
];

export default function MobileBottomNav(): JSX.Element {
  const pathname = usePathname();
  const { isAuthenticated, isLoading } = useAuth();
  const trailingItem: NavItem =
    !isLoading && isAuthenticated
      ? { href: "/for-you", label: "For you" }
      : { href: "/login", label: "Sign in" };
  const items: NavItem[] = [...BASE_ITEMS, trailingItem];
  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 backdrop-blur sm:hidden">
      <ul className="mx-auto grid max-w-6xl grid-cols-4">
        {items.map((item) => {
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
