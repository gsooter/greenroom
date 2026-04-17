/**
 * Application shell wrapping every page with nav and footer chrome.
 *
 * Server component so the nested `TopNav` can fetch its city list on
 * the server. Receives the currently selected city slug so the nav's
 * city picker can be pre-populated from whatever page is rendering.
 */

import MobileBottomNav from "@/components/layout/MobileBottomNav";
import TopNav from "@/components/layout/TopNav";

interface AppShellProps {
  children: React.ReactNode;
  selectedCitySlug?: string | null;
}

export default function AppShell({
  children,
  selectedCitySlug = null,
}: AppShellProps) {
  return (
    <div className="flex min-h-screen flex-col">
      <TopNav selectedCitySlug={selectedCitySlug} />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-6 sm:pb-10">
        {children}
      </main>
      <footer className="hidden border-t border-border bg-surface/40 sm:block">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-6 text-xs text-muted">
          <span>
            Greenroom · DMV concerts · Updated nightly
          </span>
          <span>Made for the DC, Maryland, and Virginia music scene</span>
        </div>
      </footer>
      <MobileBottomNav />
    </div>
  );
}
