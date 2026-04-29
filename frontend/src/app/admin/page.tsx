/**
 * /admin — scraper fleet + run history dashboard.
 *
 * Client-only. The admin secret never leaves the browser, so this page
 * cannot be SSR'd — `AdminKeyGate` prompts for the key, persists it in
 * `localStorage`, and feeds it into every admin API call.
 */

"use client";

import AdminKeyGate from "@/components/admin/AdminKeyGate";
import ScraperDashboard from "@/components/admin/ScraperDashboard";

export default function AdminPage(): JSX.Element {
  return (
    <AdminKeyGate>
      {(adminKey, signOut) => (
        <ScraperDashboard adminKey={adminKey} signOut={signOut} />
      )}
    </AdminKeyGate>
  );
}
