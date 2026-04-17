# DECISIONS.md — DC Concert Aggregator

Architectural decisions log. Every significant choice is recorded here with
its rationale and the alternatives considered. Check this before making
structural changes — if something is documented here, don't reverse it
without discussion.

---

## Decision Log

---

### 001 — Next.js over React/Vite

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Use Next.js with App Router instead of plain React/Vite.

**Rationale:**
The browse experience is fully public. SEO and AI discoverability are
first-class requirements, not afterthoughts. A pure SPA delivers an empty
HTML shell to search engines and AI crawlers — Next.js SSR delivers full
content on first response. The component model is identical to React so
no knowledge is lost. The React Native path for a future iOS app is
unaffected since it calls the Flask API directly.

**Alternatives considered:**
- React/Vite + prerendering plugin — fragile, incomplete, not worth it
- Remix — valid choice but Next.js has better ecosystem and Vercel support
- Pure SSR with Flask/Jinja — loses React component reusability

**Consequences:**
- Frontend deploys to Vercel instead of Railway
- Public pages are server components; authenticated pages are client components
- `generateMetadata` required on every page
- ISR (Incremental Static Regeneration) available for event pages if needed

---

### 002 — Flask REST API Backend

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Separate Flask REST API backend, not a Next.js API routes-only approach.

**Rationale:**
Celery workers, scrapers, and background jobs run in the same Python process
as Flask. Keeping backend logic in Python means one language for all server-side
work. The API is also designed to serve a future React Native iOS app — a clean
REST API is the right abstraction regardless of frontend framework.

**Alternatives considered:**
- Next.js API routes only — loses Celery, Python scraping ecosystem
- FastAPI — valid, slightly better async support, but Flask is more familiar
- GraphQL — unnecessary complexity for this data shape

---

### 003 — Spotify OAuth Only (Phase 1)

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Spotify OAuth is the only login method at launch.

**Rationale:**
For a concert app, Spotify is a feature not just auth. A user without Spotify
gets no personalization and no recommendations — there is no reason to have
an account. Every feature that requires login is powered by Spotify data.

**Provider table pattern is implemented from day one** so adding Google or Apple
OAuth in a future phase requires no schema migration — just a new provider type
in `user_oauth_providers`.

**Alternatives considered:**
- Email/password — more friction, no Spotify data, not worth the auth overhead
- Google OAuth from day one — no advantage until non-concert categories are added

**Future phase trigger:**
When expanding beyond concerts to comedy, theater, or sports — categories where
Spotify is a weak signal — add Google/Apple OAuth as an alternative. Spotify
becomes an optional enhancement rather than a requirement.

---

### 004 — PostgreSQL on Railway

**Date:** 2026-04-16
**Status:** Decided

**Decision:** PostgreSQL as the primary database, managed by Railway.

**Rationale:**
Relational data with well-defined relationships between venues, events, and users.
PostgreSQL-specific features actively used: JSONB for scraper raw data and score
breakdowns, native array types for genres and artist IDs, GIN indexes for fast
array overlap queries (the core recommendation engine query).

**Railway managed:** automatic backups, connection pooling, no operational overhead.

---

### 005 — Celery + Redis for Background Jobs

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Celery with Redis as the broker for all background work.

**Rationale:**
Nightly scraper runs, Spotify data syncs, and email digest assembly all need
to run asynchronously and on a schedule. Celery is the mature Python standard
for this. Each venue scraper runs as an isolated Celery task so one failure
doesn't cascade.

**Deployment:** Same Railway project as Flask, separate worker process.
This gives logical isolation (scraper crash doesn't kill the API) without
the operational overhead of a separate server. Can be split to a dedicated
Railway service if scraper volume grows significantly.

**Scraper schedule:** 4am ET daily. Chosen to minimize impact on venue websites
during off-peak hours and ensure fresh data for morning users.

---

### 006 — Scraper Framework Architecture

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Scraper framework with platform scrapers, a generic HTML scraper,
and custom scrapers only as a last resort. All venues declared in one config file.

**Rationale:**
A naive approach produces one bespoke scraper per venue — unmanageable at scale
and impossible for new contributors to navigate. Most venues use a small number
of ticketing platforms (Ticketmaster, Dice, Eventbrite) or follow common HTML
patterns. One scraper per platform covers the majority with zero per-venue code.

**File:** `scraper/config/venues.py` is the single source of truth. Any developer
can read this one file and understand the entire scraper fleet.

**Validation:** Post-scrape validation checks event count against 30-run historical
average. Zero results or >60% drop triggers immediate Slack + email alert.

**Alternatives considered:**
- One Python file per venue — unmaintainable at 50+ venues
- Third-party scraping service — loses visibility and control, ongoing cost

---

### 007 — Recommendation Engine Strategy Pattern

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Recommendation engine built as a list of composable scoring
strategies that each contribute a partial score.

**Rationale:**
Requirements will evolve. Starting with artist match and similar artists,
but genre scoring, popularity scoring, and social scoring are planned.
The strategy pattern means adding a scorer is a new file and a line in the
engine config — existing scorers and the engine itself never change.

**Score storage:** Every recommendation stores `score_breakdown` JSONB so:
1. Users can see why something was recommended ("Because you listen to X")
2. Developers can analyze which scorers drive actual engagement
3. Debugging a bad recommendation is straightforward

**Phases:**
- Phase 1: `ArtistMatchScorer`, `SimilarArtistScorer`
- Phase 2: `GenreScorer`, `PopularityScorer`
- Phase 3+: `FriendScorer` (if social features added)

---

### 008 — Public Browse, Login for Personalization

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Full calendar and event browse is public with no login required.
Login unlocks For You lane, saved shows, and email digests.

**Rationale:**
Lower friction for new users discovering the app. Search engines and AI crawlers
can index all event content. The app is useful as a plain calendar even without
Spotify. Users can decide to connect Spotify when they see the personalization value.

**Anonymous users get:** Full browse, filtering, search, event detail, ticket links.
**Logged-in users get:** All of the above + recommendations, saved shows, digests.

---

### 009 — SEO and AI Discoverability as Priority Features

**Date:** 2026-04-16
**Status:** Decided

**Decision:** SSR, structured data, sitemap, llms.txt, open robots.txt, and an
AI-readable plain text feed are all mandatory from day one.

**Rationale:**
The entire value of a public browse experience is that people find it. Concert
searches are high-intent. Google rich results for events (powered by MusicEvent
schema) dramatically improve click-through rates. AI chat assistants are an
emerging discovery channel — "what concerts are in DC this week" in ChatGPT
or Perplexity should cite this app. These features compound over time and are
painful to retrofit if not built in from the start.

**Implementation requirements:**
- Next.js SSR for all public pages
- `generateMetadata` on every page
- `MusicEvent` JSON-LD on every event page
- `MusicVenue` JSON-LD on every venue page
- Dynamic `sitemap.ts` covering all events and venues
- `robots.ts` explicitly allowing all major AI crawlers
- `public/llms.txt` describing site content for AI systems
- `GET /api/v1/feed/events` plain text endpoint for AI consumption

---

### 010 — SeatGeek API for Ticket Pricing (Phase 1)

**Date:** 2026-04-16
**Status:** Decided

**Decision:** SeatGeek API as primary ticket pricing source. StubHub as secondary.
TicketsData aggregator deferred until user volume justifies the cost.

**Rationale:**
SeatGeek has a free public API returning both primary and resale pricing.
Sufficient for launch. Pricing snapshots stored in `ticket_pricing_snapshots`
table so price history and trends are available from day one.

**Upgrade path:** TicketsData provides a single API covering Ticketmaster,
StubHub, SeatGeek, VividSeats, and more. Migrate when cross-platform price
comparison becomes a meaningful feature.

---

### 011 — PostHog for Analytics

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Self-hosted PostHog on Railway for product analytics.

**Rationale:**
PostHog self-hosted gives session replay, funnel analysis, feature flags,
and event tracking with all data on our infrastructure. No third-party
privacy concerns, no per-event pricing at scale, feature flags useful for
rolling out features to subsets of users. For a community app where trust
matters, keeping analytics data internal is meaningful.

**Alternatives considered:**
- Google Analytics — data leaves infrastructure, cookie consent overhead
- Plausible — privacy-forward but less powerful, no session replay or flags
- Mixpanel — expensive at scale, data leaves infrastructure

---

### 012 — SendGrid for Email

**Date:** 2026-04-16
**Status:** Decided

**Decision:** SendGrid for all transactional email and digest sending.

**Rationale:**
Generous free tier (100 emails/day), reliable deliverability, webhook support
for open and click tracking (feeds back into `email_digest_log`). Simple API
that integrates cleanly with Celery digest jobs.

---

### 013 — Railway for Backend Deployment

**Date:** 2026-04-16
**Status:** Decided

**Decision:** Railway hosts Flask API, Celery worker, Redis, and PostgreSQL
as services within a single Railway project.

**Rationale:**
Multi-service setup with minimal operational overhead. Usage-based pricing
works well for a community app with variable load. Future apps can be added
to the same Railway account. Managed PostgreSQL with automatic backups removes
operational database overhead. Vercel handles the Next.js frontend separately
(Vercel is Next.js's natural deployment target with better edge caching).

---

### 014 — City-First Data Model

**Date:** 2026-04-16
**Status:** Decided

**Decision:** All venues, events, and scrapers are scoped to a `cities` table
from day one, even though only Washington DC is active at launch.

**Rationale:**
Adding a city later should be a data operation (insert city, insert venues,
add scraper configs), not a code change. Scoping events and venues to cities
from the start means the API, frontend filters, and email digests never need
to be refactored for multi-city support.

**City expansion model:** Each new city has a community lead who submits
venues and validates data quality. Technically, expansion is a PR to
`scraper/config/venues.py` and a database seed — no structural changes.

---

### 015 — Event Type Enum with concert as Default

**Date:** 2026-04-16
**Status:** Decided

**Decision:** `event_type` enum field on all events with `concert` as the
only active value at launch. Other values (`comedy`, `theater`, `sports`,
`other`) defined in the enum but not used.

**Rationale:**
Expanding to other event categories should require no schema migration.
The filter UI, recommendation engine, and scraper framework are all designed
to be category-aware. The enum is the only change needed in the schema when
a new category is activated.

**Expansion trigger:** When Spotify becomes a weak signal for a significant
portion of users (i.e., when comedy or theater become high-demand features),
add Google/Apple OAuth (Decision 003) and activate the new event_type value.

---

### 016 — JSON-LD-First Strategy for HTML Venue Scrapers

**Date:** 2026-04-17
**Status:** Decided

**Decision:** The `GenericHtmlScraper` and any custom venue scraper under
`backend/scraper/venues/` parse schema.org Event/MusicEvent JSON-LD blocks
(`<script type="application/ld+json">`) before attempting any HTML-structure
parsing. Shared helpers live in `backend/scraper/base/jsonld.py` and
`backend/scraper/base/http.py` so custom scrapers stay tiny. Added
`beautifulsoup4` and `lxml` to backend dependencies to support robust HTML
parsing in both the extractor and custom venue scrapers.

**Rationale:**
Most modern venue sites publish JSON-LD for Google rich results, and that
format is stable, documented, and identical across sites. Scraping CSS
selectors is fragile — a redesign breaks every selector, and every venue's
markup is different. JSON-LD lets one shared extractor handle arbitrarily
many venues. When a site does not publish JSON-LD, a dedicated custom
scraper under `venues/<slug>.py` is easy to drop in (Black Cat is the first
example) because the shared HTTP and JSON-LD helpers do most of the work.

**Alternatives considered:**
- CSS-selector-based `GenericHtmlScraper` with per-venue selector maps —
  rejected because every selector becomes a config entry that needs
  maintenance and silent breakage is common.
- Headless-browser scraping (Playwright) — rejected as premature; only
  justifiable if we encounter SPAs that never server-render their events.

**Consequences:**
- New venues publishing JSON-LD require only a one-line entry in
  `scraper/config/venues.py` pointing at `GenericHtmlScraper`.
- Venues without JSON-LD get a dedicated class under `scraper/venues/`;
  the runner treats them identically to any other scraper.
- `BaseScraper.source_platform` is now a class attribute so the runner
  can attribute ingested events to the correct platform without hardcoding.

---

### 017 — Scraper Ingestion Invariants: Idempotent Re-runs

**Date:** 2026-04-17
**Status:** Decided

**Decision:** Three invariants govern how scraped events land in the database
so that every full pipeline run is idempotent — re-running the runner yields
zero new rows and zero schema changes.

1. **Event slugs are deterministic per-event.** Format:
   `<title-venue-slug>-<YYYY-MM-DD>-<6-char-sha256-of-external_id>`. Generated
   in `backend/scraper/runner.py::_generate_slug`.
2. **External IDs are stable across scrapes.** `_extract_external_id` prefers
   `raw_data["id" | "@id" | "identifier"]`, falls back to a SHA-256 hash of
   `source_url|title|starts_at.isoformat()`. The JSON-LD extractor follows the
   same rule and will never fall back to a page URL that is shared across
   events (fingerprints on `venue|title|starts_at` instead).
3. **Datetimes are stored naive in venue-local time.** Every scraper — including
   Ticketmaster, which reports `localDate`/`localTime` — yields naive datetimes.
   Timezone attachment happens at the storage/API layer, not at extraction.

**Rationale:**
Earlier code used `int(time.time())` as the slug suffix, which collided when
two same-title events landed in the same scrape-second. The JSON-LD extractor
also fell back to the page URL as external_id when the per-event `url` was
absent, collapsing every event on a page to a single row (Flash DC). Making
these three fields deterministic functions of the event's own data — not of
wall-clock time or page URL — means the dedup key `(external_id, source_platform)`
is stable and re-runs cleanly update existing rows instead of crashing on
unique-constraint violations.

**Alternatives considered:**
- UUID slugs — rejected; hostile to SEO and not human-readable.
- Timestamp-based slug suffixes — the original approach that this decision
  reverses. Caused the collisions that motivated the rewrite.
- Storing UTC datetimes with tzinfo — rejected because not every scraper has
  a reliable venue timezone, and the storage/API layer is a better place to
  attach timezone than extraction.

**Consequences:**
- Rename or rewrite of `_generate_slug` signature requires updating every
  caller and is considered a breaking change for public URLs.
- Adding a new scraper does not require any slug or external_id logic — the
  runner handles it, as long as `RawEvent.raw_data` either contains a stable
  `id` or the fallback fingerprint is unique.
- Running `backend.scripts.run_scrapers` twice in a row is a valid smoke test
  for idempotency: the second run should report `(+0 ~N =0)` for every venue.

---

### 018 — Ticketmaster Venue IDs Are Looked Up Live, Not Hand-Entered

**Date:** 2026-04-17
**Status:** Decided

**Decision:** All Ticketmaster venue IDs in `backend/scraper/config/venues.py`
are sourced from the live Discovery API (`GET /discovery/v2/venues.json?keyword=`)
and verified with `backend/scripts/smoke_ticketmaster.py` before landing in the
config. Hand-entered or inferred IDs are never trusted.

**Rationale:**
Ticketmaster venue IDs are opaque strings (e.g. `KovZpZA7knFA`). Our initial
config had 11 of 12 IDs wrong — they looked plausible but did not match any
real venue, so every scrape returned zero events. The Discovery API's venue
keyword-search endpoint returns the canonical venue ID and is the only
authoritative source. The smoke script probes every TM venue in one command
and flags any that return zero events, catching drift the moment it happens.

**Alternatives considered:**
- Derive IDs from venue URLs on Ticketmaster's consumer site — rejected; TM
  changes their URL structure periodically and consumer URLs do not always
  contain the API venue ID.
- Scrape Ticketmaster's JavaScript app to extract IDs — rejected; same fragility
  as the consumer-URL approach, plus it violates their ToS.

**Consequences:**
- Adding a new TM venue requires running
  `curl "app.ticketmaster.com/discovery/v2/venues.json?keyword=<NAME>&countryCode=US"`
  (or a follow-up helper) and copying the canonical ID.
- `smoke_ticketmaster.py` is the acceptance gate; CI or a nightly job should
  call it and alert on any venue that drops to zero.
- Zero-event status is not always a bug — some venues (e.g. Rams Head Live!)
  sell through non-TM channels and may legitimately have no events in the
  Discovery API for long stretches. The validator should track historical
  event counts per venue to distinguish real drift from expected silence.

---

## Deferred Decisions

These are known future choices that do not need to be made yet.

| Topic | Trigger to decide |
|---|---|
| Google/Apple OAuth | Expanding beyond concerts to other event types |
| TicketsData aggregator | Cross-platform price comparison becomes a core feature |
| React Native iOS app | Web app has stable active user base |
| Multi-city scraper hosting | Scraper fleet exceeds ~100 venues |
| Social features (friend activity) | Community size makes it valuable |
| Affiliate ticket links | If monetization becomes desirable |
| Full-text search engine (Elasticsearch) | PostgreSQL text search becomes a bottleneck |
