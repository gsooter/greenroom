/**
 * Browser → backend client for the email-related "me" endpoints.
 *
 * Today this is just the "send me a test email" affordance on
 * /settings/notifications. The endpoint refuses to send to bounced
 * addresses and surfaces a typed reason so the caller can render
 * the right message without parsing strings.
 */

import { fetchJson } from "@/lib/api/client";

export type TestEmailReason =
  | "sent"
  | "bounced"
  | "no_email"
  | "delivery_failed";

export interface TestEmailResult {
  sent: boolean;
  to: string;
  reason: TestEmailReason;
}

interface TestEmailResponse {
  data: TestEmailResult;
}

export async function sendTestEmailToSelf(
  token: string,
): Promise<TestEmailResult> {
  const response = await fetchJson<TestEmailResponse>("/api/v1/me/email/test", {
    method: "POST",
    token,
  });
  return response.data;
}
