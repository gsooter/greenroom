/**
 * API client for /me/notification-preferences endpoints.
 *
 * The page at /settings/notifications calls these to read and patch
 * the per-user email preference row that the backend created in the
 * email-system Phase 1 migration. Every call requires a JWT — pass
 * the token from AuthContext.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  Envelope,
  NotificationPreferences,
  NotificationPreferencesPatch,
} from "@/types";

export async function getNotificationPreferences(
  token: string,
): Promise<NotificationPreferences> {
  const res = await fetchJson<Envelope<NotificationPreferences>>(
    "/api/v1/me/notification-preferences",
    { token },
  );
  return res.data;
}

export async function updateNotificationPreferences(
  token: string,
  patch: NotificationPreferencesPatch,
): Promise<NotificationPreferences> {
  const res = await fetchJson<Envelope<NotificationPreferences>>(
    "/api/v1/me/notification-preferences",
    {
      method: "PATCH",
      token,
      body: patch,
    },
  );
  return res.data;
}

export async function pauseAllEmails(
  token: string,
): Promise<NotificationPreferences> {
  const res = await fetchJson<Envelope<NotificationPreferences>>(
    "/api/v1/me/notification-preferences/pause-all",
    { method: "POST", token },
  );
  return res.data;
}

export async function resumeAllEmails(
  token: string,
): Promise<NotificationPreferences> {
  const res = await fetchJson<Envelope<NotificationPreferences>>(
    "/api/v1/me/notification-preferences/resume-all",
    { method: "POST", token },
  );
  return res.data;
}
