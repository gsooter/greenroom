/**
 * Client-side provider wrapper mounted inside the root layout.
 *
 * Root layout is a Server Component so we can keep metadata / SSR
 * cheap; any provider that touches window/localStorage (currently
 * just AuthProvider) has to sit behind a "use client" boundary
 * below it.
 */

"use client";

import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";

export function AppProviders({ children }: { children: ReactNode }): JSX.Element {
  return <AuthProvider>{children}</AuthProvider>;
}
