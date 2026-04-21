/**
 * Application shell wrapping every page with nav and footer chrome.
 *
 * Rendered once from the root layout so every route — public or
 * authenticated — shares the same top nav, footer, and mobile bottom
 * nav. The nested `CityPicker` reads the selected city from the URL
 * search params itself, so this component needs no props.
 */

import MobileBottomNav from "@/components/layout/MobileBottomNav";
import TopNav from "@/components/layout/TopNav";
import { OnboardingBanner } from "@/components/onboarding/OnboardingBanner";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "@/lib/config";

interface AppShellProps {
  children: React.ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-screen flex-col">
      <TopNav />
      <OnboardingBanner />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-6 sm:pb-10">
        {children}
      </main>
      <footer className="border-t border-border bg-surface/40 pb-20 sm:pb-0">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1">
            <span>Greenroom · DMV concerts · Updated nightly</span>
            <span className="text-muted/80">
              Made for the DC, Maryland, and Virginia music scene
            </span>
            <span className="text-muted/80">
              Questions or feedback?{" "}
              <a
                href={SUPPORT_MAILTO}
                className="underline underline-offset-2 hover:text-foreground"
              >
                {SUPPORT_EMAIL}
              </a>
            </span>
          </div>
          <nav className="flex flex-wrap items-center gap-4">
            <a href="/events" className="hover:text-foreground">
              Events
            </a>
            <a href="/venues" className="hover:text-foreground">
              Venues
            </a>
            <a href="/about" className="hover:text-foreground">
              About
            </a>
            <a href="/sitemap.xml" className="hover:text-foreground">
              Sitemap
            </a>
          </nav>
        </div>
      </footer>
      <MobileBottomNav />
    </div>
  );
}
