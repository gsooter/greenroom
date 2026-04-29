/**
 * /admin/feedback — beta feedback triage view.
 *
 * Client-only — same constraint as the other admin pages: the admin
 * secret cannot touch a server component.
 */

"use client";

import AdminKeyGate from "@/components/admin/AdminKeyGate";
import FeedbackDashboard from "@/components/admin/FeedbackDashboard";

export default function AdminFeedbackPage(): JSX.Element {
  return (
    <AdminKeyGate>
      {(adminKey, signOut) => (
        <FeedbackDashboard adminKey={adminKey} signOut={signOut} />
      )}
    </AdminKeyGate>
  );
}
