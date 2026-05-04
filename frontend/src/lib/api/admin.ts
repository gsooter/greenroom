/**
 * Typed client functions for the `/api/v1/admin/*` endpoints.
 *
 * Every admin call sends an `X-Admin-Key` header sourced from the
 * caller (typically pulled from `localStorage` by the admin UI). The
 * backend `require_admin` decorator HMAC-compares this against
 * `ADMIN_SECRET_KEY`, so we never embed it in the client bundle.
 *
 * These calls are intentionally browser-side only — admin pages are
 * SPA-style under `/admin/*` and never SSR; the secret must never
 * touch a server component.
 */

import { config } from "@/lib/config";

const ADMIN_BASE = "/api/v1/admin";

export interface AdminScraperRun {
  id: string;
  venue_slug: string;
  scraper_class: string;
  status: "success" | "partial" | "failed";
  event_count: number;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
}

export interface AdminFleetVenue {
  slug: string;
  display_name: string;
  region: string;
  city_slug: string;
  scraper_class: string;
}

export interface AdminFleetSummary {
  enabled: number;
  by_region: Record<string, number>;
  venues: AdminFleetVenue[];
}

export interface AdminUserSummary {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
  city_id: string | null;
  music_connections: string[];
  last_login_at: string | null;
  onboarding_completed_at: string | null;
  created_at: string;
}

export interface PaginatedMeta {
  total: number;
  page: number;
  per_page: number;
  has_next: boolean;
}

interface JsonEnvelope<T> {
  data: T;
  meta?: PaginatedMeta;
}

interface AdminFetchOptions {
  method?: "GET" | "POST" | "DELETE";
  query?: Record<string, string | number | undefined>;
  adminKey: string;
  body?: unknown;
}

async function adminFetch<T>(
  path: string,
  { method = "GET", query, adminKey, body }: AdminFetchOptions,
): Promise<JsonEnvelope<T>> {
  const base = config.publicApiUrl.replace(/\/$/, "");
  const url = new URL(`${base}${ADMIN_BASE}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Admin-Key": adminKey,
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url.toString(), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let code = "ADMIN_ERROR";
    try {
      const payload = (await res.json()) as {
        error?: { code?: string; message?: string };
      };
      if (payload?.error?.code) code = payload.error.code;
      if (payload?.error?.message) message = payload.error.message;
    } catch {
      /* keep defaults */
    }
    throw new AdminApiError(res.status, code, message);
  }
  if (res.status === 204) {
    return { data: undefined as T };
  }
  return (await res.json()) as JsonEnvelope<T>;
}

export class AdminApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "AdminApiError";
  }
}

export async function getFleetSummary(
  adminKey: string,
): Promise<AdminFleetSummary> {
  const res = await adminFetch<AdminFleetSummary>("/scrapers", { adminKey });
  return res.data;
}

export async function listScraperRuns(
  adminKey: string,
  params: {
    venueSlug?: string;
    status?: "success" | "partial" | "failed";
    page?: number;
    perPage?: number;
  } = {},
): Promise<{ runs: AdminScraperRun[]; meta: PaginatedMeta }> {
  const res = await adminFetch<AdminScraperRun[]>("/scraper-runs", {
    adminKey,
    query: {
      venue_slug: params.venueSlug,
      status: params.status,
      page: params.page,
      per_page: params.perPage,
    },
  });
  return { runs: res.data, meta: res.meta as PaginatedMeta };
}

export async function triggerScraperRun(
  adminKey: string,
  venueSlug: string,
): Promise<AdminScraperRun> {
  const res = await adminFetch<AdminScraperRun>(
    `/scrapers/${encodeURIComponent(venueSlug)}/run`,
    { method: "POST", adminKey },
  );
  return res.data;
}

export interface AdminTestAlertResult {
  delivered: boolean;
  slack_configured: boolean;
  email_configured: boolean;
  title: string;
  severity: "info" | "warning" | "error";
}

export async function sendTestAlert(
  adminKey: string,
): Promise<AdminTestAlertResult> {
  const res = await adminFetch<AdminTestAlertResult>("/alerts/test", {
    method: "POST",
    adminKey,
  });
  return res.data;
}

export async function listAdminUsers(
  adminKey: string,
  params: {
    search?: string;
    isActive?: boolean;
    page?: number;
    perPage?: number;
  } = {},
): Promise<{ users: AdminUserSummary[]; meta: PaginatedMeta }> {
  const res = await adminFetch<AdminUserSummary[]>("/users", {
    adminKey,
    query: {
      search: params.search,
      is_active:
        params.isActive === undefined ? undefined : params.isActive ? "true" : "false",
      page: params.page,
      per_page: params.perPage,
    },
  });
  return { users: res.data, meta: res.meta as PaginatedMeta };
}

export async function deactivateAdminUser(
  adminKey: string,
  userId: string,
): Promise<AdminUserSummary> {
  const res = await adminFetch<AdminUserSummary>(
    `/users/${encodeURIComponent(userId)}/deactivate`,
    { method: "POST", adminKey },
  );
  return res.data;
}

export async function reactivateAdminUser(
  adminKey: string,
  userId: string,
): Promise<AdminUserSummary> {
  const res = await adminFetch<AdminUserSummary>(
    `/users/${encodeURIComponent(userId)}/reactivate`,
    { method: "POST", adminKey },
  );
  return res.data;
}

export async function deleteAdminUser(
  adminKey: string,
  userId: string,
): Promise<void> {
  await adminFetch<{ id: string; deleted: true }>(
    `/users/${encodeURIComponent(userId)}`,
    { method: "DELETE", adminKey },
  );
}

export type AdminFeedbackKind = "bug" | "feature" | "general";

export interface AdminFeedback {
  id: string;
  user_id: string | null;
  email: string | null;
  message: string;
  kind: AdminFeedbackKind;
  page_url: string | null;
  user_agent: string | null;
  is_resolved: boolean;
  created_at: string;
}

export async function listAdminFeedback(
  adminKey: string,
  params: {
    kind?: AdminFeedbackKind;
    isResolved?: boolean;
    page?: number;
    perPage?: number;
  } = {},
): Promise<{ feedback: AdminFeedback[]; meta: PaginatedMeta }> {
  const res = await adminFetch<AdminFeedback[]>("/feedback", {
    adminKey,
    query: {
      kind: params.kind,
      is_resolved:
        params.isResolved === undefined
          ? undefined
          : params.isResolved
            ? "true"
            : "false",
      page: params.page,
      per_page: params.perPage,
    },
  });
  return { feedback: res.data, meta: res.meta as PaginatedMeta };
}

export async function setAdminFeedbackResolved(
  adminKey: string,
  feedbackId: string,
  isResolved: boolean,
): Promise<AdminFeedback> {
  const res = await adminFetch<AdminFeedback>(
    `/feedback/${encodeURIComponent(feedbackId)}/resolve`,
    {
      method: "POST",
      adminKey,
      body: { is_resolved: isResolved },
    },
  );
  return res.data;
}

// ---------------------------------------------------------------------------
// Hydration
// ---------------------------------------------------------------------------

export type AdminHydrationCandidateStatus =
  | "eligible"
  | "already_exists"
  | "below_threshold"
  | "depth_exceeded";

export interface AdminArtistSummary {
  id: string;
  name: string;
  normalized_name: string;
  hydration_source: string | null;
  hydration_depth: number;
  hydrated_from_artist_id: string | null;
  hydrated_at: string | null;
}

export interface AdminHydrationCandidate {
  similar_artist_name: string;
  similar_artist_mbid: string | null;
  similarity_score: number;
  status: AdminHydrationCandidateStatus;
  existing_artist_id: string | null;
}

export interface AdminHydrationPreview {
  source_artist: AdminArtistSummary;
  candidates: AdminHydrationCandidate[];
  eligible_count: number;
  would_add_count: number;
  daily_cap_remaining: number;
  can_proceed: boolean;
  blocking_reason: string | null;
}

export interface AdminHydrationResult {
  source_artist_id: string;
  added_artists: AdminArtistSummary[];
  added_count: number;
  skipped_count: number;
  filtered_count: number;
  daily_cap_hit: boolean;
  blocking_reason: string | null;
}

export async function searchAdminArtists(
  adminKey: string,
  params: { search?: string; limit?: number } = {},
): Promise<AdminArtistSummary[]> {
  const res = await adminFetch<AdminArtistSummary[]>("/artists", {
    adminKey,
    query: { search: params.search, limit: params.limit },
  });
  return res.data;
}

export async function getHydrationPreview(
  adminKey: string,
  artistId: string,
): Promise<AdminHydrationPreview> {
  const res = await adminFetch<AdminHydrationPreview>(
    `/artists/${encodeURIComponent(artistId)}/hydration-preview`,
    { adminKey },
  );
  return res.data;
}

export async function executeHydration(
  adminKey: string,
  artistId: string,
  params: {
    adminEmail: string;
    confirmedCandidates: string[];
    immediate?: boolean;
  },
): Promise<AdminHydrationResult> {
  const res = await adminFetch<AdminHydrationResult>(
    `/artists/${encodeURIComponent(artistId)}/hydrate`,
    {
      method: "POST",
      adminKey,
      body: {
        admin_email: params.adminEmail,
        confirmed_candidates: params.confirmedCandidates,
        immediate: params.immediate ?? false,
      },
    },
  );
  return res.data;
}
