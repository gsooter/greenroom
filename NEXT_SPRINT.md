# NEXT_SPRINT.md — Greenroom

This document captures what the next sprint should tackle, drawn from
the work that just shipped and the gaps it surfaced. It is not a
backlog; it is a working-in-public outline that gets reset at the end
of each sprint.

**Last sprint closed:** 2026-04-20
**Next sprint opens:** 2026-04-21

---

## What just shipped (2026-04-14 → 2026-04-20)

### Phase 1 — Recommendation quality

- **Genre-overlap fallback in `ArtistMatchScorer`.** Users with small
  Spotify top-artist sets now see candidate events ranked by genre
  alignment instead of a bare empty state. Score breakdowns expose a
  `genre_overlap_contribution` field so the UI can later show the
  reason. Decision 035.

### Phase 2 — Venue community

- **Venue comments + votes** (schema, repository, service, API,
  frontend). Comment threads render a single hot-merge ranked list
  with a honeypot field and per-IP rate limit. Moderation via
  `hidden_at` is in place but no admin UI yet. Decision 036.

### Phase 3 — Apple Maps integration

- **MapKit JS token endpoint** with Redis-cached 25-min TTL.
- **Signed static map snapshot** (24 h cache) embedded on every venue
  page that has coordinates.
- **Get Directions deep link**, routing Apple devices to
  `maps.apple.com` and everyone else to Google.
- **Nearby POI list** via Apple's `/v1/searchNearby`, rendered SSR
  as "Grab a bite before the show". Backend access token is cached
  for its natural lifetime; POI lists cached 7 days per venue.
- Decision 037.

Tests: 587 backend / 198 frontend, all green at close of sprint.

---

## Carry-in to next sprint

These items were scoped this sprint but landed without their full
follow-through.

1. **Moderation UI for venue comments.** `hidden_at` works at the
   data layer; no admin surface yet. Currently only PostHog events
   flag suspicious traffic.
2. **Tests for `NearbyPois` and `VenueMapSnapshot` components.**
   Both server components are exercised end-to-end but have no
   direct Vitest specs; they fail quietly (render nothing) on any
   backend non-OK, which is correct but untested.
3. **Score-breakdown UI for "For You".** Decision 035 produced the
   data; the frontend still shows only the final score. Adding a
   "Why are we recommending this?" expandable row is a ~half-day task.

## Proposed work for the new sprint

Pick what fits the sprint budget; these are ranked by user impact.

1. **Ticket price freshness signals.** SeatGeek pulls are on a 6 h
   cron per Decision 020 — surface the fetched-at timestamp on the
   event card so stale prices don't read as authoritative.
2. **"Shows this weekend" digest.** The email path (Decision 033)
   is live; assembling the candidate list is a service-layer change
   off the existing For You engine. Deferred digest (Decision 021)
   is the framing; this is the simplest re-entry.
3. **Apple Music as a second music-service connect.** Key material
   is already in config (`APPLE_MUSIC_*`), and the top-artist cache
   column exists per the migration referenced in Decision 034. The
   remaining work is a service module mirroring `services/spotify.py`
   and a connect-flow route pair.
4. **Admin moderation UI.** Cover comments first, then venues. A
   single `/admin/moderation` page gated by `ADMIN_SECRET_KEY` that
   lists flagged comments and lets an operator hide/unhide.
5. **Background refresh of MapKit JS tokens.** Today, a cache miss
   on `/maps/token` incurs a synchronous JWT sign on the request
   path. A Celery beat task that refreshes the cache 2 min before
   expiry would put the hot path at zero signing work.

## Risks and unknowns

- **Apple's `/searchNearby` POI quality outside DC.** The 400 m radius
  was tuned against DC bar/restaurant density. For a future LA or
  NYC scrape the radius and limit defaults may need to scale with
  local walkability.
- **Knuckles JWKS rotation cadence.** Decision 030 caches keys for 1 h
  with a 5 min stale-while-revalidate. If Knuckles rotates mid-window,
  we accept at most 5 min of verification failures. If we see user
  reports of intermittent 401s after Knuckles deploys, shrink the
  cache window before adding failover.
- **Sprint-end tests sometimes hit real localhost Redis.** The
  `_disable_module_redis` fixture in `test_apple_maps.py` patches this
  for one module, but other modules may silently rely on real Redis
  during `pytest -k` runs. Audit is not yet scheduled.

## Decisions logged this sprint

- 035 — Genre overlap is a scoring fallback, not its own scorer.
- 036 — Venue comments use a ranked merge of hot + recent.
- 037 — Apple Maps over Google Maps for venue cartography.
