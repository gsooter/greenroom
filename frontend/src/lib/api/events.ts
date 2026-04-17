/**
 * Typed API client functions for events.
 *
 * Wraps `GET /api/v1/events` and `GET /api/v1/events/:idOrSlug`. The
 * detail endpoint accepts either a UUID or slug; callers should pass
 * whichever identifier they have.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  Envelope,
  EventDetail,
  EventStatus,
  EventSummary,
  EventType,
  Paginated,
  Region,
} from "@/types";

export interface ListEventsParams {
  region?: Region;
  cityId?: string;
  venueIds?: string[];
  dateFrom?: string;
  dateTo?: string;
  genres?: string[];
  eventType?: EventType;
  status?: EventStatus;
  page?: number;
  perPage?: number;
  revalidateSeconds?: number;
}

export async function listEvents(
  params: ListEventsParams = {},
): Promise<Paginated<EventSummary>> {
  const {
    region,
    cityId,
    venueIds,
    dateFrom,
    dateTo,
    genres,
    eventType,
    status,
    page,
    perPage,
    revalidateSeconds,
  } = params;

  return fetchJson<Paginated<EventSummary>>("/api/v1/events", {
    query: {
      region,
      city_id: cityId,
      venue_id: venueIds,
      date_from: dateFrom,
      date_to: dateTo,
      genre: genres,
      event_type: eventType,
      status,
      page,
      per_page: perPage,
    },
    revalidateSeconds,
  });
}

export async function getEvent(
  idOrSlug: string,
  revalidateSeconds?: number,
): Promise<EventDetail> {
  const res = await fetchJson<Envelope<EventDetail>>(
    `/api/v1/events/${encodeURIComponent(idOrSlug)}`,
    { revalidateSeconds },
  );
  return res.data;
}
