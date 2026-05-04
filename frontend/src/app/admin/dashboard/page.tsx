/**
 * /admin/dashboard — admin landing page summary.
 *
 * Client-only — same constraint as the rest of /admin/*: the admin
 * secret cannot touch a server component.
 */

"use client";

import AdminKeyGate from "@/components/admin/AdminKeyGate";
import Dashboard from "@/components/admin/Dashboard";

export default function AdminDashboardPage(): JSX.Element {
  return (
    <AdminKeyGate>
      {(adminKey, signOut) => (
        <Dashboard adminKey={adminKey} signOut={signOut} />
      )}
    </AdminKeyGate>
  );
}
