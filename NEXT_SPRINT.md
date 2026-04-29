# NEXT_SPRINT.md — Greenroom

This document captures what the next sprint should tackle, drawn from
the work that just shipped and the gaps it surfaced. It is not a
backlog; it is a working-in-public outline that gets reset at the end
of each sprint.

**Last sprint closed:** 2026-04-22
**Next sprint opens:** 2026-04-23

---

## What just shipped (2026-04-21 → 2026-04-22) — Apple Maps Discovery

### Phase 1 — Place verification and nearby search

- **`GET /maps/places/verify`** — round-trips user-typed queries
  through Apple's geocoder and rejects anything below a 0.80 similarity
  floor as `PLACE_NOT_VERIFIED`. Used as the spam gate on community
  recommendations. Decision 040.
- **`GET /maps/places/nearby`** — POI search around a coordinate,
  powers the recommendation form's "what place are you recommending?"
  autocomplete.

### Phase 2 — Community recommendations

- **Schema + repository + service** for map-side community pins,
  keyed by Apple Maps place ID with verified lat/lng. Vote, suppress,
  and moderation columns reuse the shape venue comments established
  in Decision 036.
- **`GET /maps/recommendations`** — bounding-box lookup with `top`/`new`
  sort and viewer-vote annotation.
- **Submit flow** enforces the verify hop on the write path so clients
  can't skip verification by fabricating the payload.

### Phase 3 — Tonight's DC Map

- **`/map` page** (SSR shell → client MapKit JS surface) renders one
  pin per tonight's DMV event, colored by genre bucket.
- **5-color bucket table** in `genre-colors.ts` collapses the
  12-entry catalogue into indie/rock, pop/folk, electronic, hip-hop,
  and jazz/soul, plus navy for everything else. Filter bar pills and
  pin colors share the table. Decision 041.
- **Recommendations overlay** layered as blush dots on the same map.

### Phase 4 — Shows Near Me

- **`GET /maps/near-me`** — day or week window, radius-bounded,
  sorted nearest-first, with `distance_km` on every row.
  In-process haversine filter, no PostGIS. Decision 042.
- **`/near-me` page** with geolocation permission gate, radius/window
  filters, map/list toggle, and a "Surprise me" button that
  randomises into a nearby event detail page.
- **Nav entries** added to `TopNav` and `MobileBottomNav`.

Tests: 9 new service tests, 7 new route tests, 7 new component tests;
all suites green at close of sprint.

---

## Carry-in to next sprint

These items were scoped this sprint but landed without their full
follow-through.

1. **Community-recommendation moderation UI.** The data model supports
   `suppressed` and `hidden_at`, and flags exist at the API, but there
   is still no admin surface — same shape as the carry-in from the
   previous sprint for venue comments. A single `/admin/moderation`
   page would cover both.
2. **Reverse-geocode the user's coord into a neighborhood label.**
   Shows Near Me currently greets the user with "Finding shows near
   you" even after location grant. A small "Shows near U Street" header
   would make the fetch feel more deliberate — Apple's `reverseGeocode`
   is already reachable from `services/apple_maps.py`.
3. **MapKit JS lazy-load test.** `initMapKit` is exercised only through
   live pages today; it has no direct Vitest coverage. Adding a script
   injector test would protect against future CDN URL changes.

## Proposed work for the new sprint

Pick what fits the sprint budget; these are ranked by user impact.

1. **Apple Music as a second music-service connect.** Carried over
   from the prior sprint. Key material already lives in config
   (`APPLE_MUSIC_*`) and the artist-cache schema is in place.
   Remaining work: a `services/apple_music.py` mirror of
   `services/spotify.py` plus a connect-flow route pair.
2. **Score-breakdown UI on For You.** Carried over. Decision 035
   produced the signal, the frontend just needs a
   "Why are we recommending this?" expandable row on the recommendation
   cards.
3. **"Shows this weekend" digest.** Carried over from the prior
   sprint. Resend path (Decision 033) is live; the assembly job is
   a service-layer call off the existing For You engine.
4. **Ticket price freshness signals.** Carried over. Surface the
   SeatGeek `fetched_at` timestamp on the event card so 6 h stale
   prices don't read as authoritative.
5. **Admin moderation UI** covering both venue comments and community
   map recommendations — carry-in item #1 expanded. Gate on
   `ADMIN_SECRET_KEY`; mirror the existing admin endpoint pattern.
6. **Near Me home-screen shortcut.** Today, a user has to navigate to
   `/near-me` and click "Use my location." If the browser already has
   a cached permission (`navigator.permissions.query`), surface a
   passive "Near you tonight" strip on `/` with a one-tap entry point.

## Risks and unknowns

- **Map surfaces and crawler budgets.** The Tonight map is SSR via a
  fallback list, which keeps it indexable, but the near-me page is
  intentionally client-only (no coordinates at render time). That is
  correct for a personal surface, but confirm sitemap entries point
  crawlers at `/map` and `/events`, not `/near-me`.
- **Apple Maps quota for the verify path.** Every community
  recommendation submission hits the geocoder; we have no
  per-session rate limit beyond the shared per-IP cap on
  `/maps/places/verify`. If the surface turns out to attract drive-by
  submissions, a per-user cap is the next lever.
- **Pin-bucket drift from the genre catalogue.** Decision 041 records
  that a new genre slug silently falls through to navy. That is the
  right default, but the reader who adds slug #13 needs to remember
  the bucket table exists — a lint rule or a dev-time assertion in
  `pinColorForGenres` would make this safer.

## Decisions logged this sprint

- 040 — Community place recommendations must clear Apple Maps verification.
- 041 — Tonight map pins collapse 12 genres into 5 color buckets.
- 042 — Shows Near Me filters distance in-process, not in PostgreSQL.
