/**
 * API client for save / unsave / list-saved event endpoints.
 *
 * All three require a JWT. Intended for client components only.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, Paginated, SavedEvent } from "@/types";

export async function saveEvent(
  token: string,
  eventId: string,
): Promise<SavedEvent> {
  const res = await fetchJson<Envelope<SavedEvent>>(
    `/api/v1/events/${eventId}/save`,
    { method: "POST", token, body: {} },
  );
  return res.data;
}

export async function unsaveEvent(
  token: string,
  eventId: string,
): Promise<void> {
  await fetchJson<void>(`/api/v1/events/${eventId}/save`, {
    method: "DELETE",
    token,
  });
}

export interface ListSavedEventsParams {
  page?: number;
  perPage?: number;
}

export async function listSavedEvents(
  token: string,
  { page = 1, perPage = 20 }: ListSavedEventsParams = {},
): Promise<Paginated<SavedEvent>> {
  return fetchJson<Paginated<SavedEvent>>("/api/v1/me/saved-events", {
    token,
    query: { page, per_page: perPage },
  });
}
