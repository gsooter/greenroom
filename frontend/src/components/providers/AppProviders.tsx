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

import { ToastProvider } from "@/components/ui/Toast";
import { AuthProvider } from "@/lib/auth";
import { SavedEventsProvider } from "@/lib/saved-events-context";

export function AppProviders({ children }: { children: ReactNode }): JSX.Element {
  return (
    <AuthProvider>
      <SavedEventsProvider>
        <ToastProvider>{children}</ToastProvider>
      </SavedEventsProvider>
    </AuthProvider>
  );
}
