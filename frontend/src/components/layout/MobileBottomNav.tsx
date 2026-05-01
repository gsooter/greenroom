/**
 * Mobile bottom navigation bar — client component.
 *
 * Rendered only below the ``sm`` breakpoint as an iOS-native four-tab
 * bar. Authenticated visitors see [Home][Events][Map][Me]; anonymous
 * visitors see [Home][Events][Map] (the auth-specific tab is dropped
 * rather than swapped, so the bar stays uncluttered for guests).
 *
 * Two routing notes shape the structure:
 *
 *   - Map is the unified tab — ``/map`` hosts both the tonight-on-map
 *     experience and the geolocated Near Me view (``?view=near-me``).
 *   - Me is the consolidated authenticated dashboard at ``/me``, which
 *     bundles For You, Saved, Following, Settings, and Sign out.
 *
 * The nav hides itself entirely on the ``/welcome`` onboarding flow so
 * the four-step sheet can own the full screen.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
  /** Routes whose pathname should also light up this tab. */
  matchPaths?: readonly string[];
}

/**
 * SVG sprite — small inline glyphs sized 24×24 to match Apple HIG mobile
 * tab bars. Inline rather than a third-party icon library because the
 * project hasn't adopted one and adding a dependency for four icons
 * isn't worth it.
 */
const ICONS = {
  home: (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9" />
    </svg>
  ),
  events: (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M3.5 9h17" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
    </svg>
  ),
  map: (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6Z" />
      <path d="M9 4v14" />
      <path d="M15 6v14" />
    </svg>
  ),
  me: (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="9" r="3.5" />
      <path d="M5 20c1.5-3.5 4-5 7-5s5.5 1.5 7 5" />
    </svg>
  ),
} as const;

const BASE_ITEMS: NavItem[] = [
  { href: "/", label: "Home", icon: ICONS.home },
  { href: "/events", label: "Events", icon: ICONS.events },
  {
    href: "/map",
    label: "Map",
    icon: ICONS.map,
    matchPaths: ["/map", "/near-me"],
  },
];

const ME_ITEM: NavItem = { href: "/me", label: "Me", icon: ICONS.me };

const HIDDEN_PATH_PREFIXES: readonly string[] = ["/welcome"];

export default function MobileBottomNav(): JSX.Element | null {
  const pathname = usePathname();
  const { isAuthenticated, isLoading } = useAuth();

  if (HIDDEN_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return null;
  }

  const showAuthed = !isLoading && isAuthenticated;
  const items: NavItem[] = showAuthed ? [...BASE_ITEMS, ME_ITEM] : BASE_ITEMS;
  const gridClass = items.length === 4 ? "grid-cols-4" : "grid-cols-3";

  return (
    <nav className="app-glass-nav app-glass-nav--bottom fixed inset-x-0 bottom-0 z-30 sm:hidden">
      <ul className={`mx-auto grid max-w-6xl ${gridClass}`}>
        {items.map((item) => (
          <li key={item.href}>
            <NavTab item={item} active={isActive(pathname, item)} />
          </li>
        ))}
      </ul>
    </nav>
  );
}

interface NavTabProps {
  item: NavItem;
  active: boolean;
}

/**
 * Renders a single mobile nav tab with stacked icon and label.
 *
 * Args:
 *     item: The nav destination (href, label, icon).
 *     active: Whether the current pathname matches this tab.
 *
 * Returns:
 *     A styled link with an icon glyph above the label and an active-
 *     state color treatment.
 */
function NavTab({ item, active }: NavTabProps): JSX.Element {
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={
        "flex flex-col items-center justify-center gap-0.5 px-2 py-2 text-[11px] font-medium " +
        (active
          ? "text-accent"
          : "text-text-primary/75 hover:text-foreground")
      }
    >
      <span aria-hidden="true">{item.icon}</span>
      <span>{item.label}</span>
    </Link>
  );
}

/**
 * Decides whether a tab should render in its active state for the
 * supplied pathname.
 *
 * Args:
 *     pathname: The current route path.
 *     item: The nav item being checked.
 *
 * Returns:
 *     ``true`` if the path matches the tab's href (or any of its
 *     declared ``matchPaths``).
 */
function isActive(pathname: string, item: NavItem): boolean {
  const candidates = item.matchPaths ?? [item.href];
  return candidates.some((candidate) =>
    candidate === "/" ? pathname === "/" : pathname.startsWith(candidate),
  );
}
