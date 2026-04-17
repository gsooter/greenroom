/**
 * Shared TypeScript types for the frontend.
 *
 * These mirror the JSON payloads returned by the Flask v1 API defined in
 * backend/api/v1/. Keep in sync with backend/services/*.py serialize_*
 * functions — if you change one, change the other.
 */

export type Region = "DMV" | (string & {});

export interface City {
  id: string;
  name: string;
  slug: string;
  state: string;
  region: Region;
  timezone: string;
  description: string | null;
  is_active: boolean;
}

export interface NestedCity {
  id: string;
  name: string;
  slug: string;
  state: string;
  region: Region;
}

export interface VenueSummary {
  id: string;
  name: string;
  slug: string;
  address: string | null;
  image_url: string | null;
  tags: string[];
  city: NestedCity | null;
}

export interface Venue {
  id: string;
  city_id: string;
  city: NestedCity | null;
  name: string;
  slug: string;
  address: string | null;
  latitude: number | null;
  longitude: number | null;
  capacity: number | null;
  website_url: string | null;
  description: string | null;
  image_url: string | null;
  tags: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface NestedVenue {
  id: string;
  name: string;
  slug: string;
  city: NestedCity | null;
}

export type EventStatus =
  | "announced"
  | "on_sale"
  | "confirmed"
  | "sold_out"
  | "cancelled"
  | "postponed";

export type EventType =
  | "concert"
  | "festival"
  | "dj_set"
  | "tour"
  | "other";

export interface EventSummary {
  id: string;
  title: string;
  slug: string;
  starts_at: string | null;
  artists: string[];
  image_url: string | null;
  min_price: number | null;
  max_price: number | null;
  status: EventStatus;
  venue: NestedVenue | null;
}

export interface EventDetail extends EventSummary {
  venue_id: string;
  description: string | null;
  event_type: EventType;
  ends_at: string | null;
  doors_at: string | null;
  genres: string[];
  spotify_artist_ids: string[];
  ticket_url: string | null;
  source_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface VenueDetail extends Venue {
  upcoming_events: EventSummary[];
  upcoming_event_count: number;
}

export interface Paginated<T> {
  data: T[];
  meta: {
    total: number;
    page: number;
    per_page: number;
    has_next: boolean;
  };
}

export interface Envelope<T> {
  data: T;
  meta?: Record<string, unknown>;
}

export interface ApiError {
  error: {
    code: string;
    message: string;
  };
}

export type DigestFrequency = "daily" | "weekly" | "never";

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  city_id: string | null;
  digest_frequency: DigestFrequency;
  genre_preferences: string[];
  notification_settings: Record<string, unknown>;
  last_login_at: string | null;
  created_at: string;
}

export interface UserPatch {
  display_name?: string | null;
  city_id?: string | null;
  digest_frequency?: DigestFrequency;
  genre_preferences?: string[] | null;
  notification_settings?: Record<string, unknown> | null;
}

export interface SavedEvent {
  saved_at: string;
  event: EventSummary;
}
