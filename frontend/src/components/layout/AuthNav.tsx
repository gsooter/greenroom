/**
 * Auth-aware navigation cluster — client component.
 *
 * Rendered inside the server-side TopNav so the rest of the shell stays
 * SSR while the sign-in / profile controls react to the client-side
 * AuthContext. While the context is hydrating, nothing is rendered to
 * avoid a visible "Sign in" flash for already-authenticated users.
 *
 * Mirrors the mobile bottom nav: instead of separate links for For You,
 * Saved, and Settings, signed-in visitors get a single "Me" entry point
 * that lands on the consolidated /me dashboard, plus a Sign out button.
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { useAuth } from "@/lib/auth";

export default function AuthNav(): JSX.Element | null {
  const router = useRouter();
  const { user, isAuthenticated, isLoading, logout } = useAuth();

  const handleLogout = useCallback((): void => {
    logout();
    router.replace("/");
  }, [logout, router]);

  if (isLoading) return null;

  if (!isAuthenticated || !user) {
    return (
      <Link
        href="/login"
        className="hidden rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:border-accent hover:text-accent sm:inline-block"
      >
        Sign in
      </Link>
    );
  }

  const label = user.display_name?.trim() || user.email;

  return (
    <div className="hidden items-center gap-2 text-sm sm:flex">
      <Link
        href="/me"
        className="max-w-[12rem] truncate rounded-md border border-border px-3 py-1.5 font-medium text-foreground hover:border-accent hover:text-accent"
        title={user.email}
      >
        {label}
      </Link>
      <button
        type="button"
        onClick={handleLogout}
        className="rounded-md px-2 py-1.5 text-xs font-medium text-muted hover:text-foreground"
      >
        Sign out
      </button>
    </div>
  );
}
