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

export type EventType = "concert" | "festival" | "dj_set" | "tour" | "other";

export interface EventSummary {
  id: string;
  title: string;
  slug: string;
  starts_at: string | null;
  artists: string[];
  genres: string[];
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
  spotify_artist_ids: string[];
  ticket_url: string | null;
  source_url: string | null;
  created_at: string;
  updated_at: string;
  pricing?: PricingState;
}

/**
 * One row of the multi-source pricing panel — mirrors the per-source
 * dict produced by `serialize_pricing_state` on the backend. Every
 * field is optional in spirit because each provider exposes a
 * different subset of the schema (TickPick has only the URL, scraper-
 * origin providers have only prices, SeatGeek has the full set).
 */
export interface PricingSource {
  source: string;
  buy_url: string | null;
  affiliate_url: string | null;
  is_active: boolean;
  currency: string;
  min_price: number | null;
  max_price: number | null;
  average_price: number | null;
  listing_count: number | null;
  last_seen_at: string | null;
  last_active_at: string | null;
}

export interface PricingState {
  refreshed_at: string | null;
  sources: PricingSource[];
}

export interface RefreshPricingResult {
  event_id: string;
  refreshed_at: string;
  cooldown_active: boolean;
  quotes_persisted: number;
  links_upserted: number;
  provider_errors: string[];
}

export interface RefreshPricingResponse {
  refresh: RefreshPricingResult;
  pricing: PricingState;
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

export interface RecommendationMatchReason {
  scorer: "artist_match" | "venue_affinity" | (string & {});
  kind:
    | "spotify_id"
    | "artist_name"
    | "genre_preference"
    | "genre_overlap"
    | "saved_venue"
    | (string & {});
  label: string;
  artist_name?: string;
  genre_slug?: string;
  genre?: string;
  venue_name?: string;
}

export interface Recommendation {
  id: string;
  score: number;
  generated_at: string | null;
  is_dismissed: boolean;
  match_reasons: RecommendationMatchReason[];
  score_breakdown: Record<string, unknown>;
  event: EventSummary;
}

export interface SpotifyTopArtist {
  id: string | null;
  name: string;
  genres: string[];
  image_url: string | null;
}

export interface SpotifyTopArtistsResponse {
  artists: SpotifyTopArtist[];
  synced_at: string | null;
}

export type VenueCommentCategory =
  | "vibes"
  | "tickets"
  | "safety"
  | "access"
  | "food_drink"
  | "other";

export type VenueCommentSort = "top" | "new";

export interface VenueComment {
  id: string;
  venue_id: string;
  user_id: string | null;
  category: VenueCommentCategory;
  body: string;
  likes: number;
  dislikes: number;
  viewer_vote: -1 | 1 | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface VenueCommentsResponse {
  data: VenueComment[];
  meta: { count: number };
}

export interface VenueCommentVoteResult {
  likes: number;
  dislikes: number;
  viewer_vote: -1 | 1 | null;
}

export type MusicProvider = "spotify" | "tidal" | "apple_music";

export interface MusicConnectionState {
  provider: MusicProvider;
  connected: boolean;
  synced_at: string | null;
  artist_count: number;
  artists: SpotifyTopArtist[];
}

export type OnboardingStepName =
  | "taste"
  | "venues"
  | "music_services"
  | "passkey";

export interface OnboardingState {
  steps: Record<OnboardingStepName, boolean>;
  completed: boolean;
  skipped_entirely_at: string | null;
  banner: {
    visible: boolean;
    dismissed_at: string | null;
    browse_sessions_since_skipped: number;
  };
}

export interface Genre {
  slug: string;
  label: string;
  emoji: string;
}

export interface ArtistSummary {
  id: string;
  name: string;
  genres: string[];
  is_followed: boolean;
}

export interface MusicConnectionsResponse {
  connections: MusicConnectionState[];
}

/**
 * A pinnable DMV event returned by `GET /api/v1/maps/tonight`.
 *
 * Shape mirrors `_serialize_tonight_event` in the backend — every
 * row is guaranteed to have a venue with non-null coordinates so
 * the map can place a pin without null-checks.
 */
export interface TonightMapEvent {
  id: string;
  slug: string;
  title: string;
  starts_at: string | null;
  artists: string[];
  genres: string[];
  image_url: string | null;
  ticket_url: string | null;
  min_price: number | null;
  max_price: number | null;
  venue: {
    id: string;
    name: string;
    slug: string;
    latitude: number;
    longitude: number;
  };
}

export interface TonightMapEnvelope {
  data: TonightMapEvent[];
  meta: {
    count: number;
    date: string;
  };
}

/**
 * A tonight-map pin augmented with `distance_km`, returned by
 * `GET /api/v1/maps/near-me`. Mirrors the tonight-pin shape with
 * an extra great-circle distance field (in km) for sorting/labels.
 */
export interface NearMeEvent extends TonightMapEvent {
  distance_km: number;
}

export type NearMeWindow = "tonight" | "week";

export interface NearMeEnvelope {
  data: NearMeEvent[];
  meta: {
    count: number;
    center: { latitude: number; longitude: number };
    radius_km: number;
    window: NearMeWindow;
    date_from: string;
    date_to: string;
  };
}

/**
 * A community recommendation overlay row returned by
 * `GET /api/v1/maps/recommendations` — structurally the same shape
 * as `_serialize_recommendation` on the backend.
 */
export interface MapRecommendation {
  id: string;
  venue_id: string | null;
  place_name: string;
  place_address: string | null;
  latitude: number;
  longitude: number;
  category: string;
  body: string;
  likes: number;
  dislikes: number;
  viewer_vote: number | null;
  suppressed: boolean;
  created_at: string;
  /**
   * Great-circle distance from the anchor venue in metres. Populated
   * by `GET /api/v1/venues/:slug/tips`; null on bbox list responses.
   */
  distance_from_venue_m?: number | null;
}
