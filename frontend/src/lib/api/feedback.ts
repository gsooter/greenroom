/**
 * Typed client for the public `POST /api/v1/feedback` endpoint.
 *
 * The submit flow is anonymous-by-default; the caller decides whether
 * to attach a bearer token. When signed in, the backend overrides the
 * `email` field with the account email so this client always sends
 * whatever the form has — no special-casing here.
 */

import { fetchJson } from "@/lib/api/client";

export type FeedbackKind = "bug" | "feature" | "general";

export interface FeedbackSubmission {
  message: string;
  kind: FeedbackKind;
  email?: string | null;
  page_url?: string | null;
}

export interface FeedbackRecord {
  id: string;
  kind: FeedbackKind;
  message: string;
  email: string | null;
  page_url: string | null;
  is_resolved: boolean;
  created_at: string;
}

export async function submitFeedback(
  payload: FeedbackSubmission,
  token: string | null = null,
): Promise<FeedbackRecord> {
  const res = await fetchJson<{ data: FeedbackRecord }>("/api/v1/feedback", {
    method: "POST",
    body: payload,
    token,
  });
  return res.data;
}
