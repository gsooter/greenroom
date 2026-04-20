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
**Status:** Superseded by Decision 032

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

### 019 — DC9 DICE Widget Watchdog Instead of a DICE Scraper

**Date:** 2026-04-18
**Status:** Decided

**Decision:** DC9 ships with `enabled=False` in `backend/scraper/config/venues.py`
and a weekly Celery watchdog (`backend.scraper.watchdogs.dc9_dice_widget.check_dc9_dice_widget`)
pings `dc9.club/events` every Monday 05:00 ET. When DC9's DICE event-list
widget is no longer HTML-commented out, the watchdog Slacks an alert so we can
re-enable the venue. No `DiceScraper` implementation is written until either
that alert fires or a DICE Partner API key lands.

**Rationale:**
As of 2026-04-18 the DICE widget on `dc9.club/events` is wrapped in
`<!-- ... -->` — there is literally nothing to scrape. The two ways to get DC9
events are both blocked: (1) direct scraping of `dice.fm/venue/...` returns 403
from Cloudflare bot-protection, and (2) `partners-endpoint.dice.fm` requires an
`x-api-key` header that DICE only issues under a Partner agreement. Writing a
stub `DiceScraper` that runs nightly against a commented-out widget would emit
pointless validator alerts for a venue we knowingly can't scrape. A weekly
watchdog trades zero ongoing noise for a one-time "time to turn DC9 back on"
ping when the source becomes tractable.

**Alternatives considered:**
- Ship a full `DiceScraper` against `partners-endpoint.dice.fm` now — rejected;
  blocked on business paperwork, not engineering.
- Scrape `dice.fm` venue pages directly — rejected; Cloudflare-blocked and
  fragile even if accessible.
- Leave DC9 silently disabled with no monitoring — rejected; means we'd only
  notice the widget came back when a user complained.
- Run the watchdog daily — rejected; venue-page HTML changes slowly and weekly
  cadence is enough signal without spamming the DC9 site or Slack.

**Consequences:**
- `backend/scraper/watchdogs/` is a new package parallel to `validator` —
  its job is to watch *external sources we don't yet scrape*, not validate
  completed scrapes. Future blocked venues (DICE-only rooms, ToS-restricted
  sites waiting on API access) belong here.
- The DC9 venue row stays in the database and in `VENUE_CONFIGS` so when the
  widget returns we only flip `enabled=True` — no re-seeding required.
- Acceptance when re-enabling DC9: wire up the real `DiceScraper`, flip
  `enabled=True`, and remove the beat entry for the watchdog in one PR.

---

### 020 — Ticketmaster `priceRanges` Is Not a Reliable Pricing Source

**Date:** 2026-04-18
**Status:** Decided

**Decision:** The Ticketmaster Discovery API's `priceRanges` field is treated
as best-effort metadata, not a guaranteed pricing source. We persist it when
present (`TicketmasterScraper._extract_prices`) but do not alert, retry, or
fall back when it is absent. Cross-venue ticket pricing is an explicit
responsibility of the SeatGeek integration once that credential lands
(Decision 010), not of the TM scraper.

**Rationale:**
Inspection on 2026-04-18 of 473 upcoming TM-sourced events showed only 47 have
a populated `min_price`, and all 47 are Howard Theatre. 9:30 Club (93 events),
The Anthem (45), Fillmore (47), and every other TM venue in the database
return zero events with `priceRanges` in their raw payloads. The field is
literally absent from the API response, not dropped by the scraper — the
extractor code is correct, the upstream data is sparse. Ticketmaster surfaces
`priceRanges` only for events that sell through their retail inventory
channel; venues on TM's presentation-only tier (most independent DC rooms)
never populate it. This is documented TM behavior, not a bug to chase.

**Alternatives considered:**
- Fall back to scraping the consumer event page for price copy — rejected;
  Ticketmaster's consumer pages are client-rendered (`curl` returns a 23-byte
  shell) so no meaningful server-side HTML exists to parse.
- Use TM's offers/inventory endpoints — rejected; those endpoints require a
  separate commerce partnership we don't have and are scoped to affiliate
  sellers, not aggregators.
- Alert when a TM venue's `min_price` coverage drops — rejected; the coverage
  is already effectively zero for 16 of 17 TM venues, so there's nothing to
  drift away from.

**Consequences:**
- `events.min_price` and `events.max_price` are understood to be nullable in
  the UI; event cards and detail pages must degrade gracefully when pricing is
  absent rather than render `$—` or `$0`.
- SeatGeek is the single source of truth for pricing breadth. When that
  integration ships, it populates `ticket_pricing_snapshots` for TM venues
  too, and the event API serializer should prefer the freshest snapshot
  across sources rather than the value extracted at scrape time.
- No code change to `TicketmasterScraper._extract_prices` — it already handles
  the present/absent cases correctly.

---

### 021 — Email Digest Deferred Out of MVP

**Date:** 2026-04-18
**Status:** Decided

**Decision:** Resend-powered weekly email digests (`backend/services/notifications.py`,
digest Celery beat entry, `/api/v1/users/me/digest-preview` endpoint) are
explicitly excluded from the MVP launch. `services/notifications.py` stays as a
one-line stub. Users can still set `digest_frequency` on their profile in
`/settings` — the field persists to the database so no data migration is needed
when the feature ships — but no email actually sends until after launch.

**Rationale:**
A transactional-email pipeline is four separate workstreams (Resend account
provisioning, HTML template, Celery schedule + job, unsubscribe flow) and each
one has its own failure surface. Shipping it poorly — stale data, broken
unsubscribe, bad template — actively damages the retention story it's supposed
to support. With zero real users at launch, there is no retention problem to
solve; the right moment for a digest is after the first 50 users have
self-selected the venues/genres they care about, which is data the digest can
then actually use. Launching without it also lets us validate whether users
come back organically (a signal that the calendar itself is sticky) before
layering email on top.

**Alternatives considered:**
- Ship a minimal "top shows this week" digest in MVP — rejected; without user
  preferences, the content is a shuffled top-20 that's worse than just
  browsing `/events`. No information gain per click.
- Use a weekly newsletter service (Mailchimp, Buttondown) with manual curation
  — rejected; breaks the "aggregator with Spotify personalization" story and
  adds an operational chore we'd have to un-do later.
- Ship transactional emails for saves/recs but skip the digest — rejected;
  same pipeline work, smaller payoff.

**Consequences:**
- `/settings` still shows the digest-frequency dropdown so the field is
  exercised end-to-end before the feature lights up. The dropdown does not
  need a warning label; users never see a "we don't actually send these yet"
  state because nobody is promised an email.
- `RESEND_API_KEY` is still listed in env vars but is allowed to be a
  placeholder at launch; production can leave it unset until the digest
  ships.
- v1.1 trigger: 50 active users OR a week of observed return-rate data,
  whichever comes first. At that point implement `services/notifications.py`,
  wire a Celery beat entry, and write the HTML template in a single PR.

---

### 022 — SeatGeek Integration Deferred Out of MVP

**Date:** 2026-04-18
**Status:** Decided

**Decision:** The SeatGeek scraper (`backend/scraper/platforms/seatgeek.py`) and
pricing service (`backend/services/tickets.py`) remain one-line stubs at
launch. Event cards and detail pages render without pricing for the ~93% of
events that lack a Ticketmaster `priceRanges` payload (per Decision 020), and
the UI degrades to "Tickets →" without a dollar figure in those cases.

**Rationale:**
SeatGeek's value is filling the pricing gap Decision 020 documents for TM
venues that don't use TM retail inventory. But: (1) SeatGeek's own inventory
heavily overlaps TM, so for many indie DC rooms (Black Cat, DC9, Pie Shop,
Comet) it has no useful data either — those venues sell directly through
their own ticket platforms (Eventbrite, Shopify, native). (2) Secondary-market
pricing on SeatGeek for small rooms is noisy and often absent. (3) The MVP
browse story doesn't require pricing — the "Tickets →" outbound link is the
actual conversion path; the price is ornamentation. Spending launch
engineering budget on a second pricing source whose marginal coverage is
unclear is poor prioritization.

**Alternatives considered:**
- Ship SeatGeek for TM venues only, skip it for indie rooms — rejected;
  halves the work but still needs the full scraper + snapshot pipeline for
  ~10 venues worth of marginal coverage, when Ticketmaster `priceRanges`
  already covers Howard Theatre reliably.
- Scrape Eventbrite pricing directly for the indie venues — rejected; the
  Eventbrite scraper is itself unbuilt, and scraping pricing is brittle
  (event-level price overrides, sold-out states, pre-sales).
- Hide "Tickets" buttons when no price is known — rejected; the outbound
  link has standalone value even without a number.

**Consequences:**
- `ticket_pricing_snapshots` table exists in the schema but stays empty at
  launch. The table is not dropped because migrating it back is worse than
  leaving it.
- `EventCard.tsx` already handles null `min_price` gracefully — no frontend
  change needed when SeatGeek ships later.
- v1.1 trigger: TM `priceRanges` coverage stays below 20% across active
  venues for 30+ days AND users are observed bouncing off "Tickets →" at
  high rates (PostHog funnel). Then implement SeatGeek + wire into
  `services/tickets.py` + add a nightly snapshot Celery task.

---

### 023 — Backend `/track` Endpoint Deferred; Frontend PostHog Only at Launch

**Date:** 2026-04-18
**Status:** Decided

**Decision:** `backend/api/v1/track.py` remains a one-line stub at MVP launch.
Analytics is entirely client-side via the PostHog JS SDK in the Next.js app,
capturing page views and core funnel events (sign-in clicked, event card
clicked, save tapped, "Tickets →" clicked) from the browser directly. No
server-side events are forwarded through the Flask API.

**Rationale:**
The PostHog JS SDK handles auto-captured page views, identifies, and custom
events end-to-end without a backend proxy. A server-side `/track` endpoint is
useful for two reasons — (1) tracking events the backend knows about but the
frontend doesn't (e.g. a scraper run completed), and (2) avoiding ad-blocker
loss by proxying through first-party DNS. Neither is load-bearing at MVP:
(1) scraper-run telemetry belongs in structured logs, not product analytics,
and (2) ad-blocker evasion is a concern for paid-acquisition funnels, not
organic DMV music discovery.

**Alternatives considered:**
- Build `/track` as a thin proxy to PostHog — rejected; duplicates work the
  client already does well, and adds a failure mode (backend down → analytics
  black hole) to a system the frontend can handle autonomously.
- Self-host PostHog proxy domain behind Next.js rewrites — deferred; not
  needed until ad-blocker loss is measurable in our own data.
- Skip PostHog entirely for MVP — rejected; we need *some* funnel data to
  calibrate the digest/SeatGeek v1.1 triggers above.

**Consequences:**
- `POSTHOG_API_KEY` is a backend env var but is unused at launch; that's
  acceptable. `NEXT_PUBLIC_POSTHOG_KEY` is the only variable that actually
  drives tracking.
- When `/track` is eventually implemented, the client-side SDK stays — the
  backend endpoint supplements rather than replaces it.
- v1.1 trigger: either (a) we add a feature that emits events only the
  backend observes (scraper-derived "X new events tonight at $venue"), or
  (b) ad-blocker loss becomes measurable and motivates a first-party proxy.

---

### 024 — User Feedback Endpoint Deferred Out of MVP

**Date:** 2026-04-18
**Status:** Decided

**Decision:** `backend/api/v1/feedback.py` stays a one-line stub. No
"not interested" / "more like this" buttons on recommendation cards at
launch, and no feedback-driven adjustment of the recommendation engine.

**Rationale:**
The feedback endpoint is designed to collect explicit user signals on
individual recommendations so the engine can deprioritize artists the user
has dismissed. That only has value once (a) there are enough users and
recommendations served that the feedback volume is meaningful, and (b) the
scorer fleet is rich enough that a per-user negative-signal layer would
actually change the ranking. Today, the engine is `ArtistMatchScorer` alone
(Decision 007 Phase 1) — explicit feedback changes nothing because the
signal is already binary ("matches an artist you listen to" or not). The
button would be UX theatre.

**Alternatives considered:**
- Ship the endpoint but route to `/dev/null` — rejected; the button implies
  a promise ("your input shapes your recs") we don't keep, which is worse
  than no button.
- Collect feedback but only show it in admin, not use it for ranking —
  rejected; same UX-theatre problem plus operational overhead.
- Ship explicit feedback and implicit feedback (save/unsave as signal) —
  implicit signal already exists via `user_saved_events`. Explicit can wait.

**Consequences:**
- Recommendation cards on `/for-you` have no dismissal UI at launch. The
  `ReasonChips` component shows *why* something was recommended; that's
  the transparency loop for MVP.
- `UserFeedback` model in `backend/data/models/` stays for schema
  compatibility but has no writes.
- v1.1 trigger: `SimilarArtistScorer` ships (Decision 007 Phase 2) AND
  active users exceed 50. At that point implicit + explicit signal
  together become worth weighting in the engine.

---

### 025 — Railway Pre-Deploy Migrations (Deferred)

**Date:** 2026-04-18
**Status:** Deferred

**Decision:** Alembic migrations are run manually via `railway run alembic upgrade head`
(or via Railway web-service shell) at MVP launch. Wiring them into a Railway
Pre-Deploy Command so every deploy runs `alembic upgrade head` automatically
is deferred until the first attempt — configured as
`cd /app/backend && alembic upgrade head` on the `web` service — can be
diagnosed (it failed on first attempt with an opaque "Pre-deploy command
failed" message that needs log inspection).

**Rationale:**
At current cadence (one developer, low migration frequency) the manual step
is acceptable and arguably safer — the migration output is read directly
before the new image goes live. Automating it is still the right long-term
call (it protects against "forgot to migrate" bugs that only surface in
runtime), but shipping it broken adds more risk than it removes. Revisit
once we have a minute to look at the actual failure log.

**Alternatives considered:**
- Ship a broken pre-deploy command and debug under pressure — rejected.
- Put the migration in the `web` service's start command — rejected; every
  replica would race on startup, and Railway scales web independently.
- Run migrations from CI on merge-to-main — rejected; the `DATABASE_URL`
  would need to be exposed to GitHub Actions, enlarging the secrets blast
  radius for marginal value over pre-deploy.

**Consequences:**
- Every new migration requires one manual `railway run` invocation before
  (or immediately after) the deploy that depends on it. Forgetting this
  results in a 500-ing web service on the new schema references — bad but
  obvious and quick to fix.
- When picked up: debug the pre-deploy failure log, wire the command on
  `web` only (not `worker`/`beat`), and verify with an empty smoke-test
  migration. Once stable, this entry moves to **Decided**.

---

### 026 — Greenroom Becomes Its Own Identity Anchor

**Date:** 2026-04-19
**Status:** Decided (supersedes Decision 003)

**Decision:** Users authenticate against a Greenroom account, not a
Spotify account. Four identity paths ship in Phase 1: WebAuthn passkey
(primary, listed first in the UI), "Sign in with Apple", "Sign in with
Google", and an email magic-link. Spotify remains a fully functional
*connected music service* that users attach from `/settings` after they
already have an account; the sync pipeline (`services/spotify.py`,
`spotify_top_artist_ids`, etc.) is unchanged — only the auth surface
moves.

**Rationale:**
Decision 003 made Spotify the only login because the MVP was a
concert-only app and every logged-in feature needed Spotify data. That
coupling now blocks three concrete things: (1) non-Spotify music
services (Apple Music, Tidal) can never be the anchor, so a user who
lives in Apple Music has to create a throwaway Spotify account to use
the app; (2) passkeys and Sign-in-with-Apple are the industry baseline
in 2026 — asking a signed-in user to also hand over Spotify scopes
before the app does anything is worse conversion than the Spotify-only
data is worth; (3) artist following (Phase 2) and genre preferences
(Phase 4) give logged-in users meaningful personalization with zero
music-service connection, so "login requires Spotify" is no longer a
product truth.

**Provider migration:** existing Spotify-authed users are not broken.
Their `users` row already has `email`, and their `user_oauth_providers`
row with `provider=spotify` already carries a stable `provider_user_id`.
JWTs have always carried the internal Greenroom `users.id` as their
`sub` (see `core/auth.issue_token`), so no session invalidation is
needed. The Spotify callback becomes a "log in *or* connect Spotify"
flow based on the caller's auth state.

**Alternatives considered:**
- Keep Spotify as the only login and add Google/Apple in a later phase
  — rejected: blocks Apple Music/Tidal users from the app entirely.
- Email/password as the primary method — rejected: passkeys and
  magic-links together cover every real user journey without the
  password-reset/credential-stuffing overhead.
- Social-only (Google + Apple) with no email path — rejected: magic
  links are critical for users who don't want a third party to know
  they use the app, and for desktop-first users without a passkey.

**Consequences:**
- `user_oauth_providers` gains `passkey` (identity) plus `apple_music`
  and `tidal` (connected music, Phase 5) enum values in the
  `20260419_auth_identity_overhaul` migration.
- New tables `magic_link_tokens` (hashed one-time tokens, 15-minute TTL)
  and `passkey_credentials` (WebAuthn public keys + sign counts).
- `users.password_hash` is added as a nullable column. The magic-link
  and passkey paths never populate it; it exists so a future
  password-reset fallback can be added without another migration.
- `services/auth.py` is the single home for all identity flows.
  `services/spotify.py` is now connection-only — no JWT issuance paths.
- Frontend `/login` becomes a 4-button stack (passkey first, then
  Apple, Google, email). Spotify connect moves to `/settings` under
  "Connected services".

---

### 027 — Magic-Link Tokens Are Hashed At Rest

**Date:** 2026-04-19
**Status:** Decided

**Decision:** The only value stored in `magic_link_tokens.token_hash` is
the SHA-256 hex digest of the raw token. The raw token exists exactly
twice: once in the outgoing email URL and once in memory during the
verify request. Verification hashes the incoming value and looks up by
the hash column.

**Rationale:**
A magic-link token is a short-lived password-equivalent. If the database
is compromised, an attacker with plaintext tokens has a 15-minute window
to log in as any user with a pending link. Storing the hash reduces that
to "hash must be inverted before the TTL expires," which is
computationally infeasible for a 32-byte random secret. The cost is
trivial — one SHA-256 per request.

**Alternatives considered:**
- Encrypt the token with an app-level key — rejected: the key lives in
  the same environment, so a breach that reads the DB can usually read
  the key too. Hashing has no decryption path by construction.
- Store plaintext and rely on short TTL alone — rejected: the TTL helps
  against *replayed* attacks but not against *concurrent* disclosure.

**Consequences:**
- `generate_magic_link(email)` returns the raw token (for the email
  body) and inserts only the hash. The raw value is never persisted.
- `verify_magic_link(token)` hashes the caller-supplied token and
  looks it up. A pure equality check on the hash column is enough.
- A nightly `cleanup_expired_magic_links` Celery task deletes rows
  whose `expires_at` is more than 24 hours old so the table stays
  small.

---

### 028 — Auth Extracted Into a Standalone Knuckles Service

**Date:** 2026-04-19
**Status:** Decided (supersedes the Greenroom-owned portions of Decisions 003 and 026)

**Decision:** All identity and authentication work moves out of Greenroom
into a separate service named **Knuckles**, deployed on its own Railway
project with its own Postgres. Knuckles owns every identity table (`users`,
`user_oauth_providers`, `magic_link_tokens`, `passkey_credentials`), every
identity endpoint (magic-link, Google, Apple, WebAuthn registration + auth,
token refresh, logout, `/me`, connected services), and JWT minting. JWTs
are signed **RS256** and consumers (Greenroom first, other apps later)
validate them locally via a **JWKS endpoint** Knuckles publishes. Every
app that talks to Knuckles registers as an `app_client` with its own
client id + secret so JWTs carry an `app_client_id` claim and tenancy is
explicit. Greenroom keeps a minimal local `users` table — `id`,
`display_name`, `avatar_url`, `created_at` — keyed by the Knuckles user
id, plus the music-data columns the recommendation engine needs
(`spotify_top_artist_ids`, `spotify_recent_artist_ids`, genre preferences,
notification settings).

**Rationale:**
Auth is not Greenroom-specific and never should have lived inside the
concert aggregator's repo. The product roadmap already assumes other
apps (a reading-list tool, a personal CRM, possibly others) that will
want the same login surface, the same social sign-in buttons, the same
passkey support, and — critically — the same user identity so one
person isn't maintaining four separate account rosters. Every week auth
lives in Greenroom is a week of "which identity decisions are
Greenroom-specific and which are universal" that has to be untangled
later. Extracting now, before the auth code has grown a long tail of
app-specific couplings, is the cheapest moment to pay this cost. The
RS256 + JWKS choice is the standard pattern for this shape: consumers
fetch the signing key once, validate offline per request, and only hit
Knuckles for the ceremonies themselves. The `app_clients` table makes
multi-tenancy explicit from day one instead of bolting it on once a
second consumer exists.

**Spotify split — corrected 2026-04-19:** The initial framing of
this decision put Spotify OAuth inside Knuckles as a "connected
service." That was wrong. Music-service OAuth is a Greenroom concern
and stays in Greenroom entirely (see Decision 029). Knuckles is
identity-only: magic-link, Google, Apple, WebAuthn. It never sees a
Spotify credential, never runs the Spotify OAuth round trip, and
never exposes a token-handoff endpoint.

**Alternatives considered:**
- **Leave auth in Greenroom and copy-paste into each future app** —
  rejected; guaranteed drift, four different magic-link TTLs, four
  different passkey RP IDs, and a user who exists in every app's
  database under a different primary key.
- **Use a managed auth provider (Auth0, Clerk, WorkOS, Supabase Auth)**
  — considered seriously. Rejected because the per-MAU pricing crosses
  into "real money" quickly for a small hobby portfolio, the passkey
  and magic-link UX on the self-hosted path is already wired, and
  owning the identity primitives means the schema can be shaped for
  the apps (app_clients, refresh-token rotation, strict-by-default
  audience claims) without bending to a vendor's model.
- **Keep HS256 with a shared secret across apps** — rejected; any app
  that can *validate* a JWT can also *mint* one, so a compromise of
  the Greenroom env vars becomes a compromise of every other app on
  the same secret. RS256 + JWKS keeps signing authority in Knuckles
  alone.
- **Do the extraction gradually, leaving auth in both places during
  migration** — rejected; dual-write on identity is the standard way
  to ship a "which system is the source of truth" bug. One cutover is
  shorter and less error-prone than a migration window.

**Consequences:**
- **A new repo and Railway project** — Knuckles is a sibling deployment,
  not a Greenroom subdirectory. The Knuckles repo has its own CI,
  migrations, tests, and on-call story.
- **Greenroom loses 1100+ lines of auth service code, plus the routes,
  tests, migrations, and frontend context that rely on it.** The remaining
  Greenroom `users` table is a foreign-key target only; no identity
  logic lives server-side in the aggregator anymore.
- **Greenroom's `core/auth.py` becomes a JWKS verifier.** It fetches
  Knuckles' public key on boot (with an on-disk fallback cache for
  resilience), validates RS256 JWTs per request, and extracts `sub`
  as the Knuckles user id. No signing happens in Greenroom ever again.
- **Refresh-token rotation exists for the first time.** Knuckles issues
  1h access + 30d refresh tokens, rotates on use, and invalidates old
  refresh tokens on logout. The frontend needs a refresh hook before
  access-token expiry.
- **Existing Greenroom JWTs become invalid at cutover.** HS256 tokens
  signed with the Greenroom JWT secret will not validate against
  Knuckles' public key. Every user signs in again on first visit
  post-migration. Acceptable: the user count is tiny and the magic-link
  / passkey path is already fast.
- **Greenroom env gains `KNUCKLES_URL` and `KNUCKLES_CLIENT_ID` (plus
  `KNUCKLES_CLIENT_SECRET` for server-to-server calls). It loses
  `JWT_SECRET_KEY`, `GOOGLE_*`, `APPLE_*`, `WEBAUTHN_*`, `RESEND_*`
  (for magic-link delivery — that moves with auth). Spotify env vars
  (`SPOTIFY_*`) stay in Greenroom — see Decision 029.**
- **Data migration runs once:** existing Greenroom `users` rows are
  copied into Knuckles with preserved UUIDs; `google` and `apple`
  provider rows are copied into Knuckles; `spotify` (and future
  `apple_music` / `tidal`) provider rows are migrated into Greenroom's
  new `music_service_connections` table; magic-link and passkey rows
  move to Knuckles. Greenroom's local `users` table is rewritten to
  the minimal shape with the same UUIDs as foreign keys. Saved
  events, recommendations, and notification settings keep their
  `user_id` references unchanged.
- **Frontend auth context (`frontend/src/lib/auth.tsx`) repoints to
  Knuckles.** Login, register, passkey ceremony, and session refresh
  all talk to `KNUCKLES_URL`; the Greenroom API only receives an
  `Authorization: Bearer <jwt>` header and never participates in the
  auth round trip.
- **`AUTH_MIGRATION.md` is the working plan** and tracks what's moved,
  what's left, and the operational checklist for cutover. It should
  be deleted once the migration is complete and this decision entry
  remains as the permanent record.

---

### 029 — Music-Service OAuth Stays In Greenroom; Knuckles Is Identity-Only

**Date:** 2026-04-19
**Status:** Decided (clarifies the scope of Decision 028)

**Decision:** Spotify, Apple Music, Tidal, and any other music-service
OAuth lives entirely in Greenroom. Greenroom owns a new local
`music_service_connections` table (`id`, `user_id` = Knuckles UUID,
`service`, `access_token`, `refresh_token`, `token_expires_at`,
`scopes`, `created_at`). Greenroom owns the OAuth routes at
`backend/api/v1/music/` and the settings UI for connect/disconnect.
Knuckles never sees any music-service credential, runs any music-
service ceremony, or exposes any music-service endpoint. Knuckles'
`user_oauth_providers.provider` enum is restricted to `{google, apple}`.

**Rationale:**
A centralized auth service is only valuable if its surface stays small
and universal across consumers. The moment Knuckles knows about
Spotify, every future consuming app has to reason about "does my app
use Spotify or not" when asking Knuckles for a user. That couples
Knuckles to a specific product's data model and turns it from
identity-infrastructure into a leaky shared backend. Music-service
tokens also have a fundamentally different lifecycle — they refresh
frequently, they carry per-app scopes, and the data they gate (listening
history, library) is only meaningful inside the consuming app's feature
set. Keeping music services in Greenroom means one app owns the full
picture of one concern, rather than two apps sharing an awkward split
across an HTTP boundary. This decision also removes the server-to-server
Spotify token handoff endpoint that Decision 028 originally proposed —
a nice simplification because no credential ever leaves Greenroom.

**Alternatives considered:**
- **Knuckles owns the Spotify OAuth round trip, Greenroom fetches
  tokens server-to-server** (the original Decision 028 framing) —
  rejected per the rationale above. Adds a failure mode without
  removing any coupling; Knuckles still knows Spotify exists.
- **Spotify-only hybrid: Knuckles holds Spotify credentials because
  Decision 026 called Spotify a "connected service"** — rejected.
  The word "connected service" from Decision 026 was about Greenroom
  UX ("after you sign in, you may connect Spotify"), not about where
  the OAuth lives. This decision pins the infrastructure question
  explicitly.
- **Leave all auth in Greenroom** — rejected. That contradicts
  Decision 028; the extraction is still the right call. This decision
  is about the *scope* of what moves, not whether anything moves.

**Consequences:**
- Knuckles' `CLAUDE.md` and `DECISIONS.md` encode music-service
  exclusion as a hard rule. A future Claude Code session that tries
  to add `spotify` / `apple_music` / `tidal` to Knuckles will see
  the rule at the top of CLAUDE.md and must reject the change.
- Greenroom introduces `music_service_connections` in a fresh
  migration as part of Phase 2 of the Knuckles cutover. The old
  `user_oauth_providers` rows for music services are migrated into
  the new table; Google/Apple rows go to Knuckles.
- `backend/api/v1/auth.py` (Spotify routes) is not deleted — it's
  renamed/rewired to `backend/api/v1/music/spotify.py`, pointed at
  the new table, and no longer issues JWTs. The JWT issuance is
  gone because Spotify OAuth doesn't create a new Greenroom user
  account anymore; an unauthenticated user must first sign in via
  Knuckles (magic-link / Google / Apple / passkey) before they can
  connect Spotify.
- `backend/services/spotify.py` (sync path) stays put; it reads the
  current access token from `music_service_connections` instead of
  `user_oauth_providers`. No Knuckles HTTP call on the sync path.
- `SPOTIFY_CLIENT_ID / _SECRET / _REDIRECT_URI` stay in Greenroom's
  environment — they do not appear in any Knuckles env file.

---

### 030 — Greenroom Verifies Knuckles Tokens Locally Against a Cached JWKS

**Date:** 2026-04-19
**Status:** Decided

**Decision:** Greenroom validates every incoming access token against
the Knuckles JWKS in-process. The JWKS is fetched once over HTTP,
cached in memory keyed by ``kid`` for one hour, and re-fetched
immediately on a cache miss (Knuckles key rotation). Greenroom never
calls Knuckles to validate a token on the request path.

**Rationale:** This is the standard RS256 + JWKS pattern and is the
whole reason Knuckles publishes a JWKS in the first place. Local
verification keeps the auth check on Greenroom's hot path at
microseconds (an asymmetric signature verify, no network) and means
Knuckles being briefly unreachable does not 503 the entire
authenticated surface of Greenroom. The kid-miss-refresh path makes
key rotation safe without coordinated deploys: Knuckles starts
issuing tokens with a new ``kid``, the first such token Greenroom
sees triggers a JWKS refresh, and verification proceeds.

**Alternatives considered:**
- **Token introspection (call Knuckles ``/v1/auth/introspect`` per
  request)** — rejected. Adds a synchronous network hop to every
  authenticated Greenroom request, couples uptime to Knuckles
  uptime, and defeats the entire purpose of asymmetric signing.
  Reasonable for opaque tokens, wasteful for JWTs.
- **Use ``jwt.PyJWKClient`` directly** — rejected. Convenient but
  caches keys forever (no TTL knob) and has no controllable
  rotation refresh. The custom cache here is ~30 lines and we
  control the failure modes.
- **No caching, fetch JWKS per verify** — rejected. Same network-
  coupling problem as introspection plus much higher latency.

**Consequences:**
- A new ``backend.core.knuckles_client`` module owns the JWKS cache
  and the small HTTP client used for app-client proxy calls
  (magic-link start, token exchange, passkey ceremonies).
  ``backend.core.auth`` is not modified yet — this commit is
  additive. Wiring ``require_auth`` to call
  :func:`verify_knuckles_token` is the next step in the cutover.
- Three new env vars in Greenroom: ``KNUCKLES_URL``,
  ``KNUCKLES_CLIENT_ID``, ``KNUCKLES_CLIENT_SECRET``. A fourth
  optional one (``KNUCKLES_JWKS_CACHE_TTL_SECONDS``) defaults to
  3600. All four ship as empty strings / defaults so the module
  imports cleanly even before the Knuckles app-client is registered.
- A disk-cached JWKS fallback for "Knuckles down at process start"
  is deferred to Phase 3 hardening; the in-memory cache is
  sufficient until then because tokens already in flight remain
  verifiable through one hour of Knuckles downtime.
- ``PyJWT`` dependency upgraded to ``PyJWT[crypto]`` so the
  ``cryptography`` extras (RS256 verify) are pinned explicitly
  rather than picked up transitively.

---

### 031 — Greenroom Users Are Lazily Provisioned From Knuckles Claims

**Date:** 2026-04-19
**Status:** Decided

**Decision:** Greenroom does not keep a pre-populated user directory.
The first authenticated request from a Knuckles-signed token with a
``sub`` that has no matching Greenroom ``users`` row inserts that row
on the fly, keyed by the Knuckles user UUID, using ``email`` (and
``name`` when present) from the token claims. After Decision 030 the
legacy HS256 ``issue_token`` / ``verify_token`` helpers are also
removed; Greenroom no longer signs or verifies any token format
except the Knuckles-issued RS256 access tokens that flow through
``verify_knuckles_token``. The Spotify OAuth routes shift from a
sign-in flow to a connect flow — both endpoints now require an
existing Knuckles session and the happy path returns only the updated
user profile, never a session token.

**Rationale:** Two of Greenroom's concerns collapse into one step
this way. (1) There is no "sync users from Knuckles" background job
to keep correct — the first real authenticated request does it for
free. (2) ``require_auth`` always produces a concrete ``User`` row
for downstream code, so no view has to defensively handle "token
valid but no local profile yet." The Spotify-connect reframing falls
out naturally: with Knuckles as the sole identity issuer, Spotify
OAuth cannot be a sign-in path without Greenroom re-entering the
token-minting business it just exited.

**Alternatives considered:**
- **Pre-provision on the Knuckles side via a webhook/outbound event
  on signup.** Rejected. It introduces an at-least-once delivery
  problem (retries, duplicate handling, backfill for missed events)
  to solve a problem the first real request already solves for
  free. Webhooks earn their keep for cross-service state that *must*
  be consistent before the user acts, which this is not.
- **Error out with 401 until a user manually "activates" their
  Greenroom profile.** Rejected. The extra screen adds zero value —
  the account already exists in Knuckles and the user already
  consented at signup there. A silent first-hit provision matches
  the mental model.
- **Keep the legacy HS256 ``issue_token`` helper around as dead
  code "just in case."** Rejected. ``require_auth`` is the only
  caller that mattered; leaving unused token-issuance helpers in a
  security-adjacent module is an invitation to reintroduce a
  parallel auth path by accident.

**Consequences:**
- ``backend.core.auth.issue_token`` and ``verify_token`` are gone,
  along with ``backend/tests/core/test_auth.py``. ``require_auth``
  is now the whole surface of the module.
- ``users_repo.create_user`` accepts an optional ``user_id`` so the
  provision path can pin the PK to the Knuckles UUID. Existing
  callers that omit it keep their old behavior (fresh UUID).
- Knuckles must include the ``email`` claim on every access token
  it issues — Greenroom treats a missing email as an invalid token
  because it cannot stand up a profile without one. A future
  ``/v1/auth/me``-style enrichment from Knuckles would remove that
  constraint; until then, the claim requirement is hard.
- The Spotify routes now 401 unauthenticated callers. The frontend
  "connect Spotify" UI must attach the existing Knuckles bearer to
  both ``/auth/spotify/start`` and ``/auth/spotify/complete``, and
  stop expecting a ``token`` field in the complete response.
- ``/auth/spotify/complete`` rejects re-linking a Spotify profile
  that already points at a different Greenroom user with a 409.
  That blocks the account-takeover path where an attacker re-
  consents through their own Knuckles login.

---

### 032 — Greenroom Proxies Knuckles Identity Endpoints Server-Side

**Date:** 2026-04-19
**Status:** Decided

**Decision:** The browser never talks to Knuckles. Every identity
ceremony the frontend needs — magic-link start/verify, Google
start/complete, Apple start/complete, passkey register begin/complete,
passkey sign-in begin/complete — is exposed on Greenroom at the
existing ``/api/v1/auth/*`` paths and forwarded to Knuckles from the
server via ``backend.core.knuckles_client.post``. Greenroom's
server-side env holds the ``X-Client-Id`` / ``X-Client-Secret``
pair; the secret never appears in a bundle, a cookie, or a response
body. The session-completing proxies (``magic-link/verify``,
``google/complete``, ``apple/complete``, ``passkey/authenticate/complete``)
verify the Knuckles-issued access token, lazily provision the
Greenroom ``users`` row, and return a normalized ``{token, user}``
envelope that the frontend AuthContext already consumes.

**Rationale:** Knuckles Decision 007 is explicit: every Knuckles
auth endpoint requires app-client credentials and there is no
"public-client" escape hatch. Respecting that from Greenroom is the
default-safe posture — a compromised SPA bundle cannot by itself
mint tokens, because the bundle never held the secret in the first
place. The server-side proxy path also keeps the frontend API surface
stable (the existing ``auth-identity.ts`` client is unchanged), so
nothing the user sees or bookmarks has to move.

**Alternatives considered:**
- **Point the frontend at ``NEXT_PUBLIC_KNUCKLES_URL`` directly and
  add a "public client" mode to Knuckles.** Rejected. It would
  require reversing Knuckles Decision 007, weakening the security
  model for every consuming app, not just Greenroom. Public clients
  also force origin-allowlist + PKCE plumbing on Knuckles that the
  confidential-client path sidesteps entirely.
- **Split identity into two bundles — one public (no secret) and one
  confidential (Greenroom's).** Rejected. Doubles the deploy surface
  and the config drift risk for one frontend's convenience.
- **Have the frontend call Knuckles directly with the secret baked
  into the bundle.** Rejected on sight — it leaks the secret to every
  browser tab and to any script injected into the SPA.

**Consequences:**
- ``backend/api/v1/auth_identity.py`` is the authoritative list of
  identity proxies. New Knuckles auth endpoints have to be added
  here before the frontend can call them.
- ``knuckles_client.post`` now accepts a ``bearer_token`` kwarg so
  proxies for Knuckles' bearer-auth endpoints (passkey register) can
  forward the caller's token alongside the app-client headers.
- Greenroom owns the ``redirect_url`` for each ceremony, filled in
  from ``settings.frontend_base_url``. The frontend no longer sends
  a redirect URL at all — one less field the SPA can tamper with.
- The normalized ``{token, user}`` envelope now only surfaces the
  access token; the Knuckles refresh token stays on the Knuckles
  response and is dropped at the proxy boundary. Refresh-token
  handling is deferred to a later decision; until then a session
  lives exactly as long as the Knuckles access-token TTL.
- The ``/auth/logout`` endpoint is unchanged and still lives in
  ``auth_session.py`` — it is a no-op server-side and does not need
  to round-trip to Knuckles.

---

### 033 — Resend Replaces SendGrid for Transactional Email

**Date:** 2026-04-20
**Status:** Decided

**Decision:** Greenroom and Knuckles both send transactional email
through Resend. SendGrid is removed as a dependency from both repos.
The DB column `email_digest_log.sendgrid_message_id` is renamed to
`provider_message_id` so a future provider change does not require
another schema migration.

**Rationale:**
Resend's developer ergonomics are meaningfully better than SendGrid's
for the volume Greenroom actually sends: a single HTTP endpoint
(`POST /emails`), one Bearer token, no SDK to carry. The SendGrid SDK
pulled in a starlette transitive that mypy had to ignore globally;
dropping it removes that override. Resend's free tier (3,000/mo,
100/day) comfortably covers the MVP digest + scraper-alert traffic.

**Alternatives considered:**
- Stay on SendGrid — rejected; the SDK adds weight and the API
  surface is larger than we use.
- Use the SendGrid REST API directly without the SDK — rejected; it
  avoids the SDK dependency but keeps us on a provider whose free
  tier was recently cut and whose deliverability story for
  transactional-only senders has regressed.
- AWS SES — rejected; SES requires sandbox-exit paperwork and a
  verified domain with DKIM before any recipient outside a verified
  allowlist can receive mail. Too much operational setup for the
  volume.

**Consequences:**
- `services/email.py` in both repos calls `POST
  https://api.resend.com/emails` directly via `requests`. No SDK.
- `backend/scraper/notifier.py` routes its email fallback through
  the same `send_email` seam — the scraper no longer carries its own
  SendGrid import.
- `RESEND_API_KEY` / `RESEND_FROM_EMAIL` replace the SendGrid env
  vars in both repos' `.env.example`, CI workflow, and dev configs.
- Decision 012 is marked Superseded.
- Decision 028's env-loss list updated to reference `RESEND_*`
  instead of `SENDGRID_*` for magic-link delivery moving to Knuckles.

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
| Automated Railway pre-deploy migrations | Once pre-deploy failure log (2026-04-18) is diagnosed — see Decision 025 |
