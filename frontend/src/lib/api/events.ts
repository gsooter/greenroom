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
  PricingState,
  Region,
  RefreshPricingResponse,
} from "@/types";

export interface ListEventsParams {
  region?: Region;
  cityId?: string;
  venueIds?: string[];
  dateFrom?: string;
  dateTo?: string;
  genres?: string[];
  artistIds?: string[];
  artistSearch?: string;
  priceMax?: number;
  freeOnly?: boolean;
  availableOnly?: boolean;
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
    artistIds,
    artistSearch,
    priceMax,
    freeOnly,
    availableOnly,
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
      artist_id: artistIds,
      artist_search: artistSearch,
      price_max: priceMax,
      free_only: freeOnly ? "true" : undefined,
      available_only: availableOnly ? "true" : undefined,
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

/**
 * Fetch the latest persisted pricing state for an event without
 * triggering an upstream sweep. Used by the client-side refresh
 * panel to rehydrate after a manual refresh, and by anywhere that
 * needs the panel without re-fetching the whole event payload.
 */
export async function getEventPricing(
  idOrSlug: string,
  revalidateSeconds?: number,
): Promise<PricingState> {
  const res = await fetchJson<Envelope<PricingState>>(
    `/api/v1/events/${encodeURIComponent(idOrSlug)}/pricing`,
    { revalidateSeconds },
  );
  return res.data;
}

/**
 * Trigger a manual pricing sweep for one event. The backend enforces
 * a 5-minute cooldown shared across every visitor — a request inside
 * the window short-circuits to the persisted state and sets
 * `cooldown_active: true` on the returned `refresh` summary.
 */
export async function refreshEventPricing(
  idOrSlug: string,
): Promise<RefreshPricingResponse> {
  const res = await fetchJson<Envelope<RefreshPricingResponse>>(
    `/api/v1/events/${encodeURIComponent(idOrSlug)}/refresh-pricing`,
    { method: "POST", revalidateSeconds: 0 },
  );
  return res.data;
}
