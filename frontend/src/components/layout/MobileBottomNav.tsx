/**
 * Mobile bottom navigation bar — client component.
 *
 * Rendered only below the `sm` breakpoint. Highlights the active route
 * by checking the current pathname so the user always knows which
 * section of the app they're in.
 *
 * The desktop TopNav hosts Saved, Settings, and Sign out inside
 * ``AuthNav`` — every one of those is gated behind ``sm:flex``, which
 * means on mobile they're unreachable without typing the URL. The
 * "Me" tab below exposes them through a small menu anchored above the
 * nav so a signed-in mobile user can reach their account in one tap.
 */

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
}

const BASE_ITEMS: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/events", label: "Events" },
  { href: "/map", label: "Tonight" },
  { href: "/near-me", label: "Near Me" },
  { href: "/venues", label: "Venues" },
];

const ME_ROUTES: readonly string[] = ["/saved", "/settings"];

export default function MobileBottomNav(): JSX.Element {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, isLoading, logout } = useAuth();
  const [isMeOpen, setIsMeOpen] = useState<boolean>(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const closeMenu = useCallback((): void => {
    setIsMeOpen(false);
  }, []);

  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  useEffect(() => {
    if (!isMeOpen) return;
    const handleClick = (event: MouseEvent): void => {
      if (!menuRef.current) return;
      if (menuRef.current.contains(event.target as Node)) return;
      closeMenu();
    };
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") closeMenu();
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [isMeOpen, closeMenu]);

  const handleSignOut = useCallback((): void => {
    closeMenu();
    logout();
    router.replace("/");
  }, [closeMenu, logout, router]);

  const showAuthed = !isLoading && isAuthenticated;
  const showGuest = !isLoading && !isAuthenticated;
  const meActive = ME_ROUTES.some((route) => pathname.startsWith(route));
  const gridClass = showAuthed ? "grid-cols-7" : "grid-cols-6";

  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 backdrop-blur sm:hidden">
      {isMeOpen && showAuthed ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Account menu"
          className="absolute bottom-full right-2 mb-2 w-52 overflow-hidden rounded-lg border border-border bg-bg-white shadow-lg"
        >
          <Link
            href="/saved"
            role="menuitem"
            className="block px-4 py-3 text-sm font-medium text-text-primary hover:bg-bg-surface"
          >
            Saved
          </Link>
          <Link
            href="/settings"
            role="menuitem"
            className="block border-t border-border px-4 py-3 text-sm font-medium text-text-primary hover:bg-bg-surface"
          >
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={handleSignOut}
            className="block w-full border-t border-border px-4 py-3 text-left text-sm font-medium text-blush-accent hover:bg-blush-soft/60"
          >
            Sign out
          </button>
        </div>
      ) : null}

      <ul className={`mx-auto grid max-w-6xl ${gridClass}`}>
        {BASE_ITEMS.map((item) => (
          <li key={item.href}>
            <NavTab
              item={item}
              active={
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href)
              }
            />
          </li>
        ))}
        {showAuthed ? (
          <>
            <li>
              <NavTab
                item={{ href: "/for-you", label: "For you" }}
                active={pathname.startsWith("/for-you")}
              />
            </li>
            <li>
              <button
                type="button"
                onClick={() => setIsMeOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={isMeOpen}
                aria-label="Account menu"
                className={
                  "flex w-full items-center justify-center px-2 py-3 text-xs font-medium " +
                  (meActive || isMeOpen
                    ? "text-accent"
                    : "text-muted hover:text-foreground")
                }
              >
                Me
              </button>
            </li>
          </>
        ) : null}
        {showGuest ? (
          <li>
            <NavTab
              item={{ href: "/login", label: "Sign in" }}
              active={pathname.startsWith("/login")}
            />
          </li>
        ) : null}
      </ul>
    </nav>
  );
}

interface NavTabProps {
  item: NavItem;
  active: boolean;
}

/**
 * Renders a single mobile nav tab as a Next.js Link.
 *
 * @param item - The nav destination (href + label).
 * @param active - Whether the current pathname matches this tab.
 * @returns A styled link with active-state coloring.
 */
function NavTab({ item, active }: NavTabProps): JSX.Element {
  return (
    <Link
      href={item.href}
      className={
        "flex items-center justify-center px-2 py-3 text-xs font-medium " +
        (active ? "text-accent" : "text-muted hover:text-foreground")
      }
    >
      {item.label}
    </Link>
  );
}
