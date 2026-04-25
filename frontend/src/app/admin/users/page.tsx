/**
 * /admin/users — Greenroom user-profile management.
 *
 * Client-only — same constraint as the scraper admin page: the admin
 * secret cannot touch a server component.
 */

"use client";

import AdminKeyGate from "@/components/admin/AdminKeyGate";
import UserDashboard from "@/components/admin/UserDashboard";

export default function AdminUsersPage(): JSX.Element {
  return (
    <AdminKeyGate>
      {(adminKey, signOut) => (
        <UserDashboard adminKey={adminKey} signOut={signOut} />
      )}
    </AdminKeyGate>
  );
}
