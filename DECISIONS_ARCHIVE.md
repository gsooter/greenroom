# DECISIONS_ARCHIVE.md — Greenroom Decision Archive

Full historical record of architectural decisions with rationale, alternatives
considered, and consequences. This file is **not** loaded into Claude Code's
working context by default — it is consulted only when full reasoning is needed
on a specific decision.

For the active index of current decisions, see `DECISIONS.md`. New decisions
should be added there as one-line entries. Only add a full archive entry here
when the decision is complex enough that future engineers will genuinely need
to re-read the alternatives and reasoning.

Entries marked **Superseded** or **Removed** in `DECISIONS.md` remain here for
historical reference but do not reflect current architecture.

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
**Status:** Superseded by Decision 047

**Decision:** SeatGeek API as primary ticket pricing source. StubHub as secondary.
TicketsData aggregator deferred until user volume justifies the cost.

**Rationale:**
SeatGeek has a free public API returning both primary and resale pricing.
Sufficient for launch. Pricing snapshots stored in `ticket_pricing_snapshots`
table so price history and trends are available from day one.

**Upgrade path:** TicketsData provides a single API covering Ticketmaster,
StubHub, SeatGeek, VividSeats, and more. Migrate when cross-platform price
comparison becomes a meaningful feature.

**Why superseded:** SeatGeek-only coverage left every Tier B venue
(DICE, the venue-direct ticketers, Eventbrite) without any pricing at
all, and a single-source price was misleading on shows where the
secondary market diverged sharply from the primary. Decision 047
replaces this with a provider-registry that fans out across SeatGeek,
Ticketmaster, TickPick, and the existing scrapers.

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

### 025 — Railway Pre-Deploy Migrations (Superseded by Decision 034)

**Date:** 2026-04-18
**Status:** Superseded

**Decision:** Alembic migrations are run manually via `railway run alembic upgrade head`
(or via Railway web-service shell) at MVP launch. Wiring them into a Railway
Pre-Deploy Command so every deploy runs `alembic upgrade head` automatically
is deferred until the first attempt — configured as
`cd /app/backend && alembic upgrade head` on the `web` service — can be
diagnosed (it failed on first attempt with an opaque "Pre-deploy command
failed" message that needs log inspection).

**Superseded:** See Decision 034. Migrations now run in the prod image's
CMD rather than as a Railway Pre-Deploy Command, sidestepping the opaque
Pre-Deploy failure entirely.

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

### 034 — Migrations Run From the Prod Image CMD

**Date:** 2026-04-20
**Status:** Decided (supersedes Decision 025)

**Decision:** The prod stage of `backend/Dockerfile` runs
`alembic upgrade head` immediately before `gunicorn` starts, so every
Railway deploy applies pending migrations as part of container startup.
The Railway Pre-Deploy Command is not used.

**Rationale:**
Decision 025 deferred automation because the Railway Pre-Deploy Command
approach failed with an opaque error that would have taken real debugging
time to unpack. Putting the migration in the image's CMD gets the same
"no deploy ships on a stale schema" guarantee with no Railway-specific
configuration — the image is self-contained and the migration output
appears inline in the `web` service logs. Railway runs a single instance
of the web service in this project, so the startup-race concern that
motivated the original Pre-Deploy preference is moot; the `worker` and
`beat` services use a different image target (`dev`/base) and do not
run migrations.

This was picked up after a magic-link sign-in 500'd in prod because a
pending migration (`add_tidal_and_apple_music_top_artist_caches_to_users`)
had not been applied. Manual `railway run alembic upgrade head` was
blocked by Pydantic Settings refusing to import without every env var
populated, so the shortest path to a correct state was to let the
deploying image run its own migrations.

**Alternatives considered:**
- **Return to Railway Pre-Deploy Command** — rejected for now; the
  opaque failure mode from Decision 025 has not been diagnosed and the
  CMD approach already meets the correctness bar.
- **Run migrations from CI on merge-to-main** — rejected; `DATABASE_URL`
  would have to be exposed to GitHub Actions, and CI-driven migrations
  can race a slow deploy.
- **Keep migrations manual and document the step** — rejected; Decision
  025's manual path just got caught by the exact failure mode it warned
  about.

**Consequences:**
- If the web service scales to more than one replica, migrations will
  race on startup. Before enabling horizontal scale, move the migration
  step to a one-shot Railway job or re-adopt Pre-Deploy.
- A migration that fails will block the container from starting —
  the deploy fails loudly, which is the desired behavior. Gunicorn
  never serves traffic against a half-migrated schema.
- The CMD uses shell form (`cd ... && alembic ... && cd ... && gunicorn ...`)
  so ``${PORT:-5001}`` expands as expected and the Alembic config path
  resolves against `/app/backend`. Keep it shell form if touching this
  line.

---

### 035 — Genre Overlap Is a Scoring Fallback, Not Its Own Scorer

**Date:** 2026-04-20
**Status:** Decided

**Decision:** The `ArtistMatchScorer` falls back to a genre-overlap
sub-score when no exact artist match exists, instead of adding a
separate `GenreMatchScorer` to the engine pipeline. Genre overlap
contributes at most 0.4 of the scorer's output weight; an exact artist
hit still dominates the signal.

**Rationale:**
Keeps every candidate event scoring on one consistent axis from the
user's perspective ("did Greenroom find someone I listen to?"). A
dedicated genre scorer would fire for every candidate and dilute the
recommendations with "some band I've never heard of, but they're also
indie" — the MVP goal is to surface shows the user would have heard
about anyway, not to expand taste. Blending inside the existing scorer
also means the engine's 0.0-1.0 normalization stays intact with no
weight-tuning across scorers.

**Alternatives considered:**
- **Standalone `GenreMatchScorer`** — rejected; creates weighting
  problems between scorers and makes the score breakdown harder to
  explain to a user. ("We recommended this because genres match" is
  a weaker reason than "we recommended this because you listen to
  the opener.")
- **No fallback at all** — rejected; users with small Spotify top-artist
  sets (freshly-linked accounts or casual listeners) saw empty For You
  pages every week.

**Consequences:**
- The score breakdown stored in `recommendations.score_breakdown` now
  has an optional `genre_overlap_contribution` field; existing
  breakdowns without it are still valid.
- Future scorers that want to use genre data should read
  `ArtistMatchScorer.genre_cache` rather than re-fetching.

---

### 036 — Venue Comments Use a Ranked Merge of Hot + Recent

**Date:** 2026-04-20
**Status:** Decided

**Decision:** Venue comment threads render a single chronological list
ordered by a hot-merge score that blends net votes and recency, rather
than separate "Top" and "New" tabs. The top slot is reserved for a
pinned staff comment if one exists.

**Rationale:**
Reddit-style "Top vs New" tabs don't carry weight on venue pages —
traffic per venue per day is low enough that "New" is almost always
empty and "Top" is almost always one comment from 2023. A ranked merge
surfaces a useful thread immediately without making the user choose a
sort. The formula is `log(1 + max(0, net_votes)) + recency_decay(age)`,
which reduces to strict recency when a thread is young and to net-vote
order once the page has been live for a while.

**Alternatives considered:**
- **Top / New tabs** — rejected; low per-venue traffic means both tabs
  look wrong most of the time.
- **Strict recency only** — rejected; incentivizes spam and buries
  high-signal comments behind one-liners.
- **Strict net-vote ranking** — rejected; fresh comments on established
  venues would never surface.

**Consequences:**
- Backend repository returns already-ranked rows; the frontend does no
  client-side re-sort. Vote mutations re-query the server.
- Hiding a comment (via moderation) drops it from the ranked list but
  leaves the row in place for audit — callers filter on `hidden_at`.
- A later "controversial" or "contested" sort would require a second
  ranking function; none is planned.

---

### 037 — Apple Maps Over Google Maps for Venue Cartography

**Date:** 2026-04-20
**Status:** Decided

**Decision:** Venue pages render static map snapshots, mint MapKit JS
tokens, and pull nearby-POI results from Apple's Maps Web APIs. Google
Maps is used only as a fallback destination for the "Get Directions"
deep link on non-Apple devices.

**Rationale:**
Apple's MapKit APIs (MapKit JS + Snapshot + Maps Server API) are free
for the usage pattern Greenroom has — a few thousand venue-page
snapshots per day, one MapKit JS instance per active tab. Google Maps
Platform bills per static-image and per Places-Search request, and the
venue page's "grab a bite before the show" list would turn into the
single biggest cost center in the backend within the first month.
Apple's `searchNearby` results are comparable in quality to Google's
Places for the DC bar/restaurant density Greenroom cares about.

**Alternatives considered:**
- **Google Maps Platform** — rejected on cost; a rough model put the
  POI-search line alone at $200-$400/month at launch traffic, with no
  caching ceiling.
- **Mapbox + Overture / Foursquare POI feeds** — rejected; the stack
  would be cheaper than Google but not Apple, and it introduces three
  vendor relationships where one suffices.
- **No map at all, just an address link** — rejected; the venue page
  is a conversion surface for "which show tonight," and the map
  snapshot measurably lifts click-through to Get Directions in
  competitor products.

**Consequences:**
- The backend signs ES256 JWTs for MapKit JS tokens *and* raw ECDSA
  P-256 signatures (r||s) for Snapshot URLs — they share a key but
  the signing primitives differ.
- `fetch_nearby_poi` requires an access-token exchange via
  `maps-api.apple.com/v1/token`; the access token is cached in Redis
  for its natural lifetime minus a 60s safety margin.
- `APPLE_MUSIC_PRIVATE_KEY` and `APPLE_MAPKIT_PRIVATE_KEY` overlap in
  ownership (the Apple Developer account) but are distinct keys in
  config. Don't collapse them.
- The "Get Directions" button uses a UA sniff to route Apple devices
  to `maps.apple.com` and everyone else to Google. The button is a
  client component that hydrates into its final href to avoid an SSR
  hydration mismatch.

---

### 038 — Onboarding "Skip" Marks a Step Complete Without Writing Any Data

**Date:** 2026-04-20
**Status:** Decided

**Decision:** Every step of the /welcome flow has a "Skip for now"
affordance. Skipping stamps the step's `*_completed_at` timestamp on
`user_onboarding_state` but writes none of the step's data. A separate
`skipped_entirely_at` timestamp is set only when the user skips *every*
step in one go — that is the sole trigger for the browse-page
"Finish setup" nudge banner.

**Rationale:**
The onboarding funnel has to serve two populations simultaneously: new
signups walking the wizard end-to-end, and users who bailed on a
previous /welcome attempt. If "skip" left the per-step `completed_at`
null, the gate in the auth callback (`resolvePostAuthDestination`)
would re-trap returning users on the same step they skipped earlier,
which is the opposite of the intent — the skip button is a promise
that the user will not be re-prompted until they go dig up the nudge
themselves. Marking each step complete on skip also keeps the progress
chips meaningful: a completed row with no preferences set is still a
completed row, and the dashboard can distinguish "genuinely empty"
from "never onboarded" via `skipped_entirely_at`.

**Alternatives considered:**
- **Leave completed_at null on skip; track a separate skipped_at
  column per step.** Rejected — doubles the schema surface and still
  needs identical "has the user seen this step" logic in two places.
  The gate only cares whether the user has seen the step, not *how*
  they got past it.
- **Treat skip as a write of `{}` (empty genres, zero venues).**
  Rejected — cannot distinguish "I dislike everything" from "I haven't
  told you yet," which matters for the recommendation engine's cold-
  start behavior. Scorers need to know whether to fall back on popular
  shows or trust the user's explicit negative signal.
- **Hard-block the user from browsing until they finish.** Rejected —
  hostile to the "tourist checking what's on tonight in DC" use case,
  which is a first-class browse experience per the SSR-for-SEO rule.

**Consequences:**
- The banner cannot be driven off per-step timestamps; it's derived
  from `skipped_entirely_at` plus `browse_sessions_since_skipped`
  plus `banner_dismissed_at`. Any one of those being non-null / >=7
  hides it.
- Auto-hide fires at 7 browse sessions (`_BANNER_AUTO_HIDE_AFTER_SESSIONS`
  in `backend/services/onboarding.py`). Sessions are bumped client-side
  at most once per `sessionStorage` window via the
  `greenroom.browse_session_bumped` key — route transitions inside the
  same tab should not count as new sessions.
- The Spotify/Tidal OAuth round-trip breaks the in-page wizard state,
  so `MusicServicesStep` stashes `greenroom.welcome_return=music_services`
  before redirect and the shared callback helper reads it through
  `consumeWelcomeReturnFlag` (one-shot — read-and-delete). Without that
  marker, a user who connected Spotify from /welcome would land on
  /for-you with an un-acknowledged passkey step.
- Passkey auto-completes when the user signed in via passkey — holding
  the key is proof the step is already done, so `/login` stamps
  `passkey_completed_at` on successful passkey auth.

---

### 039 — Genre Catalog Is Canonical on the Backend, Fetched Over HTTP

**Date:** 2026-04-20
**Status:** Decided

**Decision:** The 12-entry genre catalog lives in `backend/core/genres.py`
as a `GENRE_SLUGS: frozenset[str]` plus a TypedDict list of labels and
emojis. The /welcome UI reads it from the public `GET /api/v1/genres`
endpoint rather than hardcoding the list in the Next.js bundle. The
`_coerce_genre_list` validator on PATCH /me rejects any slug not in
the frozenset.

**Rationale:**
Genres are reference data that shows up in three places: the
onboarding TasteStep tiles, the event-card filter chips, and the
recommendation engine's genre-overlap fallback (Decision 035). If the
list diverges across those three, the engine silently drops matches,
and the UI displays chips that the API would reject. Single source of
truth on the backend keeps `PATCH /me` validation, the scorer's genre
universe, and the wizard tiles in lockstep — adding a genre is a
single-file change.

**Alternatives considered:**
- **Hardcode the list in TypeScript.** Rejected — the frontend is a
  client-side recommendation consumer, not the authority on what
  counts as a genre. Backend changes would silently desync the UI.
- **Store genres as a table, seed via migration.** Overkill for 12
  rows that change maybe once a year, and it introduces a DB
  round-trip on a page that's already fetching three other state
  slices. Revisit if the catalog exceeds ~50 entries or becomes
  user-curatable.

**Consequences:**
- `listGenres()` does not require auth and is cacheable at the CDN.
- The scorer in `recommendations/scorers/` imports `GENRE_SLUGS`
  directly; never hardcode a genre string in a scorer.
- Renaming a slug is a breaking change — existing `genre_preferences`
  arrays would point to a now-invalid slug. If the catalog ever needs
  to rename, write a migration that rewrites stored preferences in
  the same commit.

---

### 040 — Community Place Recommendations Must Clear Apple Maps Verification

**Date:** 2026-04-22
**Status:** Decided

**Decision:** Every community recommendation submitted through the map
form must round-trip through Apple Maps' geocoder via
`GET /api/v1/maps/places/verify` before it can be saved. The backend
accepts the recommendation only when Apple returns a candidate whose
name (or address) clears a 0.80 Jaro-Winkler similarity floor against
the user-typed query. Recommendations that fail verification are
rejected with `PLACE_NOT_VERIFIED` (404); nothing is stored.

**Rationale:**
Community pins are free-text, user-generated content that will be
shown on a map alongside curated shows. Without a verification gate,
the path of least resistance for a spammer is typing a business name
that doesn't exist and getting a blush dot on the map forever. Running
every submission through Apple's real-world geocoder forces each
recommendation to correspond to a place that actually exists, at
coordinates Apple will confirm. The 0.80 similarity floor is what
filters out cases where Apple cheerfully returns the nearest
something-similar even when the query is noise. Using Apple as the
verifier is free for our usage pattern (Decision 037) and means the
pin's lat/lng is authoritative rather than whatever the client sent.

**Alternatives considered:**
- **No verification, trust the client's lat/lng.** Rejected — fake
  pins are the obvious attack and no amount of rate-limiting fixes a
  drive-by submission of garbage coordinates.
- **Verify only on display, not on submit.** Rejected — lets bad data
  into the database and pushes filtering onto every read path instead
  of the single write path.
- **Require a venue-slug from our existing venue table.** Rejected —
  the community pin surface is explicitly for non-venue places
  ("grab a bite before the show"), so constraining to `venues` would
  defeat the feature. The Apple Maps catalogue is the right universe.

**Consequences:**
- The submit-recommendation flow is two hops: frontend calls
  `/maps/places/verify` first, then submits the verified payload to
  `POST /api/v1/recommendations`. The service layer re-verifies on
  the write path so a client can't forge a verified flag.
- Apple Maps outages (`APPLE_MAPS_UNAVAILABLE`, 503/502) propagate as
  submission failures rather than silent saves. Acceptable — writes
  are rare, the alternative is unvetted pins.
- The similarity floor lives in `backend/services/apple_maps.py` as
  `_PLACE_VERIFY_SIMILARITY_FLOOR = 0.80`. Treat it as a tuning knob:
  if false rejections dominate support traffic, loosen it before
  changing the verification architecture.

---

### 041 — Tonight Map Pins Collapse 12 Genres Into 5 Color Buckets

**Date:** 2026-04-22
**Status:** Decided

**Decision:** The Tonight map encodes an event's genre on the pin as
one of 5 color buckets (plus a navy default when nothing matches):
indie/rock → green, pop/folk → blush, electronic → amber, hip-hop →
coral, jazz/soul → gold. The bucket table is a frontend-only resource
in `frontend/src/lib/genre-colors.ts` and is referenced by both the
filter bar and the pin render path. The canonical 12-slug genre
catalogue (Decision 039) stays unchanged.

**Rationale:**
The catalogue optimizes for recommendation signal quality — fine
slicing of electronic vs. techno vs. house is load-bearing for the
scorer. The map optimizes for a single glance across the city. At
12 distinct pin colors, the map becomes a pointillist blur that no
legend can anchor; at 5 colors, a user can scan and say "the green
pins are the indie shows tonight." Collapsing on the client keeps the
decision reversible — if a future phase wants per-slug pins, the
bucket table is deleted without touching the backend or the scorer.

**Alternatives considered:**
- **One pin color per canonical genre slug.** Rejected — the legend
  would be longer than the map is wide, and most DMV nights have
  fewer than 40 shows total, so the long tail of slugs contributes
  nothing to the overview.
- **Push the bucketing into the backend as a `pin_bucket` column.**
  Rejected — the mapping is presentation-layer metadata, it would
  bloat every tonight-map payload, and the frontend already imports
  the bucket table to drive the filter bar's color swatches.
- **Let the user pick their own color scheme.** Rejected — scope
  creep for a discovery surface; the point is speed, not
  customization.

**Consequences:**
- Adding a canonical genre slug (per Decision 039) should be followed
  by a decision about which bucket it lands in. A slug without an
  entry in the bucket table is safe — it falls through to navy — but
  the filter bar's "Indie / Rock" pill will not catch it until the
  table is updated.
- The filter bar (`FilterBar.tsx`) is the UI surface that names the
  buckets. Changing a bucket's display label is a one-file edit there;
  changing the slug-to-bucket mapping is a one-file edit in
  `genre-colors.ts`.
- Pin color tokens (`--color-amber`, `--color-coral`, `--color-gold`)
  are declared in `globals.css` alongside the existing palette. Do
  not introduce new pin colors without a bucket or the legend drifts.

---

### 042 — Shows Near Me Filters Distance In-Process, Not in PostgreSQL

**Date:** 2026-04-22
**Status:** Decided

**Decision:** `GET /api/v1/maps/near-me` fetches the day-windowed
event list from `events_repo.list_events` with a generous `per_page`,
then filters by great-circle distance in Python using a haversine
helper (`_haversine_km` in `backend/services/events.py`). The
repository does not know about coordinates; the database schema does
not carry a geometry column.

**Rationale:**
PostGIS would be the textbook answer, but the DMV venue set is
under 100 rows, the query is already bounded by a day/week window
filter, and the post-fetch Python loop finishes in well under a
millisecond. Standing up PostGIS — or even adding a raw lat/lng
index and a `ST_DWithin` bypass — would add a migration, a build-time
system dependency, and a cross-cutting repository concern for a
problem the CPU solves instantly. Keeping the filter at the service
layer also leaves every other event query path untouched.

**Alternatives considered:**
- **Add PostGIS and a `ST_DWithin` clause on `venues.geom`.**
  Rejected now, revisit at the scale threshold below. The migration
  is non-reversible without data loss once other callers start
  relying on geometry columns.
- **Bounding-box prefilter in SQL, haversine in Python.** Rejected —
  at DMV volume the SQL prefilter saves zero wall-clock time and
  doubles the number of places the distance calculation logic lives.
- **Cache the haversine-filtered results per (lat, lng, radius, window).**
  Rejected — too many free variables for the hit rate to be
  meaningful; the raw query is already fast enough.

**Consequences:**
- Revisit this decision when any of the following are true: the
  venue set grows past ~1,000 rows, a second distance-filtered route
  ships, or the `per_page=200` ceiling on `list_events` starts
  truncating day-windowed results. At that point, PostGIS with a
  gist index on `venues.geom` is the migration.
- The service caps `limit` to 100 and the route clamps `radius_km`
  to `[0.5, 100]` so a pathological request can't force the repo to
  materialize the full events table. These are the load-bearing
  bounds; don't remove them without a PostGIS backing.
- The returned envelope carries `distance_km` on every row. This is
  computed in the same loop that filters, so the sort is free. Do
  not reintroduce a separate distance fetch on the frontend.

---

### 043 — Dice.fm Scraper Uses JSON-LD, Not CSS Selectors

**Date:** 2026-04-24
**Status:** Decided

**Decision:** `DiceScraper` (`backend/scraper/platforms/dice.py`)
parses the `schema.org` JSON-LD `Place.event` array that dice.fm
embeds in every venue page's `<script type="application/ld+json">`.
The scraper falls back to the page's `__NEXT_DATA__` bootstrap JSON
when JSON-LD carries no events, and raises `DiceScraperError` only
when both sources are absent or empty. One scraper class serves
every Dice-ticketed DMV venue (DC9, BERHTA, Songbyrd, Byrdland);
each is registered in `scraper/config/venues.py` with its
`dice_venue_url` in `platform_config`.

**Rationale:**
Dice's venue pages are a Next.js SPA. CSS selectors over the
rendered DOM are both brittle (class names are hash-suffixed and
change on every deploy) and require a headless browser to execute,
which triples infra cost and adds flake. JSON-LD, by contrast, is
contractual output Dice maintains for Google rich-results parity:
stable field names (`startDate`, `offers`, `performer`, `url`),
machine-readable timestamps with timezone offsets, and a single
`Place.event` array covering every upcoming show at the venue.
`__NEXT_DATA__` exists as a defensive fallback — occasionally
Dice's server renders the page before hydration populates the
JSON-LD event list, in which case the bootstrap payload at
`props.pageProps.profile.sections[*].events` still carries the
full lineup (with prices in cents under `price.amount`).

**Alternatives considered:**
- **Playwright/Selenium headless browser.** Rejected — adds a
  Chromium dependency and a multi-second render budget per venue
  for data we can pull out of the initial HTML response.
- **Official Dice partner API.** No such public API exists.
  Reaching out for partnership would delay launch by weeks for a
  worse contract than the public JSON-LD Dice already maintains.
- **Scrape `__NEXT_DATA__` as primary source.** Rejected — it is
  a private implementation detail and its shape has shifted in the
  past (nested under different section keys). JSON-LD's schema.org
  contract is more stable.
- **CSS selectors over server-rendered HTML.** Rejected — Dice's
  event list hydrates client-side; there is no pre-hydration DOM
  to select against without a headless browser.

**Consequences:**
- Adding a new Dice-ticketed venue is one config entry plus a
  `llms.txt` line — no new scraper code.
- If Dice ever removes the JSON-LD block, `_parse_next_data()`
  keeps the fleet alive for at least one deploy cycle while we
  rewrite. The validator's zero-result alert (`backend/scraper/
  validator.py`) will fire if both sources disappear.
- The scraper enforces a 2 s inter-request delay and retries once
  on `requests.ConnectionError` with a 5 s backoff. These are
  tuned against dice.fm's observed rate-limit behaviour; do not
  lower them without testing against production traffic.
- Dice event URLs are the `source_url`, and (when offers carry no
  explicit URL) the `ticket_url` as well. The event URL is also
  stamped into `RawEvent.raw_data["id"]` so the runner's idempotent
  upsert key is stable across scrapes.
- Tests (`backend/tests/scraper/test_dice.py`) mock every HTTP call
  via the `responses` library and monkeypatch `time.sleep`/
  `time.monotonic`, so the suite never touches dice.fm and the
  rate-limit guard can be exercised in microseconds.

---

### 044 — Apple Music Listening Signals: Library + Recently Played + Heavy Rotation

**Date:** 2026-04-24
**Status:** Decided

**Decision:** `backend/services/apple_music.py::sync_top_artists` pulls
three Apple Music endpoints on every sync and merges them into
`users.apple_top_artists`:

1. `GET /v1/me/library/artists` — the breadth signal (library).
2. `GET /v1/me/recent/played/tracks` — the recency signal, flattened
   to unique artists by `artistName`.
3. `GET /v1/me/history/heavy-rotation` — the dominant-taste signal,
   albums flattened to unique artists by `artistName` (playlists are
   skipped — a curator is not a listening signal about a specific
   artist).

Each merged entry carries a `source` ∈ `{heavy_rotation,
recently_played, library}` and an `affinity_score` ∈ `{0.9, 0.6, 0.4}`.
Duplicates across sources collapse by normalized artist name; the
highest-affinity source wins, genres are unioned, and a real Apple
library id (`l.*`) is preserved over any synthetic `am:name:*` id from
the recently-played / heavy-rotation flatteners. The persisted list
is ordered affinity-descending.

A new Celery task `backend.services.apple_music_tasks
.sync_user_apple_music_data` mirrors the Spotify task so the sync can
be re-triggered off-request. It is not wired into the beat schedule —
Apple Music re-syncs happen on connect today, same as Spotify; the
task exists so a future nightly or reconnect-triggered refresh is a
one-line addition.

**Rationale:**
Before this change the scorer saw only a user's Apple Music *library*,
which is a breadth signal with no recency. A user who has 400 saved
artists but actively listens to four of them would get the same
treatment as someone who saves only the things they play — the scorer
couldn't tell a dead entry from a heavy rotation. Adding recently-played
and heavy-rotation brings the Apple Music signal to parity with
Spotify's `top + recently-played` pair. Heavy rotation in particular
is Apple's *own* "most played" bucket — a stronger signal than anything
Spotify surfaces without a top-artists pull.

**Alternatives considered:**

- **Create a `user_artist_affinity` table keyed by
  `(user_id, artist_name, source)` and retire the per-provider JSONB
  columns.** This is cleaner long-term and what the sprint prompt
  originally described. Rejected for this change because (1) it
  forces a cross-cutting refactor of `ArtistMatchScorer`, `Spotify
  sync_top_artists`, `Tidal sync_top_artists`, and every repository
  that reads the existing caches; (2) asymmetric introduction — only
  Apple Music on the new table, Spotify/Tidal on the old columns —
  would be worse than either endpoint. Per-entry `source` /
  `affinity_score` fields inside the existing JSONB are
  forward-compatible: the future affinity table is a migration that
  reads these fields out of every provider's JSONB in one shot. When
  that migration ships, this decision updates to Superseded.
- **Hydrate the library-artist list with catalog genres via
  `/v1/catalog/{storefront}/artists/{ids}`.** Deferred. Heavy-rotation
  and recently-played payloads already carry `genreNames`, which
  backfills most library entries via the merge-and-union step. A
  dedicated genre-enrichment pass can land later if gaps show up in
  the genre-overlap tier of the scorer.
- **Skip heavy rotation and rely on recently-played alone.** Rejected.
  Heavy rotation is specifically the signal a listener would label
  "this is what I listen to"; recently-played is noisier (background
  plays, throwaway listens). Dropping heavy rotation loses the best
  signal Apple exposes.

**Consequences:**

- `users.apple_top_artists` now contains entries shaped
  `{id, name, genres, image_url, source, affinity_score}`. Existing
  rows written by the pre-change sync are still valid — the scorer
  ignores the new fields and library-only data remains
  meaningful. A reconnect refreshes any account in-place.
- A stale Music User Token surfaces a 401/502 from Apple on any of
  the three endpoints. The library fetch remains load-bearing (its
  failure still propagates); recently-played and heavy-rotation
  failures are swallowed and logged so a flaky endpoint does not
  prevent the other signals from persisting.
- `PROVIDER_SIGNAL_NOTE.apple_music` in
  `frontend/src/app/settings/page.tsx` is updated to advertise the
  expanded signal set.
- No MusicKit scope changes are needed. Apple Music does not use
  OAuth scopes — a Music User Token granted at authorize time gates
  all three endpoints uniformly.
- No new environment variables. The existing Apple Developer
  credentials cover every endpoint.

---

### 045 — Venue Coverage Audit Uses Discovery API Event Counts as Ground Truth

**Date:** 2026-04-25
**Status:** Decided

**Decision:** When auditing `scraper/config/venues.py` for missing or
broken entries, the only ground truth we trust is the live
Ticketmaster Discovery API event count for a candidate `venue_id`.
A scraper-config entry is added or kept only when
`/discovery/v2/events.json?venueId=<id>&classificationName=Music`
returns a non-zero `totalElements`, and the address / lat-long
copied into `seed_dmv.VENUE_METADATA` is the value the Discovery
API returns for that same id.

**Rationale:** The previous expansion (commit 534b64e) already used
this approach for the 19 venues it added. The 2026-04-25 audit
revealed two failure modes the public Ticketmaster website papers
over:

1. **Wrong umbrella id.** Wolf Trap registered with id
   `KovZpZAtvJeA` (Wolf Trap, the property) returned 4 upcoming
   music events. The Filene Center's own id `KovZpZAEetJA` returned
   52. The website routes both to the same listing page, so a human
   spot-check would look correct, but the API silos them.
2. **Silently zero.** Rams Head Live! Baltimore returned 0 upcoming
   music events (`KovZpZAFk6tA`). Without an API check the scraper
   would have continued to run nightly against a dead id, and the
   validator only flags scrapers whose count drops below 40 % of
   their *historical* mean — a venue that has *always* been zero
   never trips it.

Pinning the audit to the API also lets us mechanically reject
look-alike ids (e.g. "City Winery - DC" and "City Winery
Washington D.C." both exist in the venue table; both return 0
events because the venue ticketed off-platform). We don't add
either.

**Alternatives considered:**

- **Trust the public Ticketmaster website.** Rejected: routes
  multiple venue ids to a single canonical listing page so the
  silos stay invisible.
- **Pre-emptively add every plausible DC-area venue and let the
  validator alert on zero counts.** Rejected: adds noise to the
  alert channel and wastes nightly fetch budget on dead ids.
- **Crawl the Discovery API once per audit and auto-generate the
  config.** Deferred: doing this by hand once per quarter is fine
  while the venue list is in the low hundreds. Worth revisiting
  when expanding to a new metro.

**Consequences:**

- Eight venues added in this audit:
  Wolf Trap Filene Center (52 ev), Tally Ho Theater (43 ev),
  The Theater at MGM National Harbor (27 ev), Music Center at
  Strathmore (18 ev), The Innsbrook Pavilion (14 ev), The
  Kennedy Center Concert Hall (7 ev), Ember Music Hall (7 ev),
  State Theatre Falls Church (1 ev). Five new cities seeded:
  `falls-church-va`, `leesburg-va`, `north-bethesda-md`,
  `national-harbor-md`, `glen-allen-va`.
- Rams Head Live! parked with `enabled=False` and an inline
  comment explaining the zero-count audit result. The slug stays
  registered so re-enabling is a one-line change once a working
  source is identified.
- A new test module `backend/tests/scraper/test_venues_config.py`
  locks in the structural invariants of the config (no duplicate
  slugs, every city referenced has a seed, every venue has
  metadata, every scraper class is importable, TM and Dice configs
  carry their required platform_config keys) and pins the eight
  audit-added venues by id. A future refactor can't silently drop
  them.
- Venues whose actual ticketing is off-platform — The Hamilton,
  Pearl Street Warehouse, Sixth & I, City Winery DC, Jammin Java,
  Bethesda Blues & Jazz, The Camel — are *not* added with a TM
  scraper (each shows 0 events). They are tracked in this entry
  as candidates for a later GenericHtmlScraper pass.

---

### 046 — Scraper Alert Pipeline Layers Six Independent Signals With Per-Severity Cooldowns

**Date:** 2026-04-25
**Status:** Decided

**Decision:** The scraper alerting layer is composed of six independent
signals — `zero_results`, `event_drop`, `scraper_failed`, `escalation`,
`stale_data`, and `fleet_failure` — each with its own stable
`alert_key` and a severity-specific cooldown window. A daily digest
(`07:30 ET`) and an admin "send test alert" button complete the
pipeline. Cooldown state is persisted in `scraper_alerts`; lookups
fail open (a broken dedup table never silences alerts).

Cooldown windows:

| alert_key prefix | severity | cooldown |
|---|---|---|
| `zero_results:<slug>` | error | 12 h |
| `event_drop:<slug>` | warning | 12 h |
| `scraper_failed:<slug>` | error | 6 h |
| `escalation:<slug>` | error | 24 h |
| `stale_data:<slug>` | warning | 48 h |
| `fleet_failure` | error | 2 h |

**Rationale:** A single broken venue would otherwise post on every
nightly run *and* every manual `/admin` re-trigger, drowning the
on-call channel inside a day. Per-severity cooldowns reflect how often
the operator actually wants to be reminded — a 6 h window for a fresh
failure (so a same-day fix gets a fresh ping) but 48 h for stale data
(silent-failure mode that doesn't get worse when ignored). Layering
six independent signals catches the failure modes that one signal
would miss: `zero_results` and `event_drop` need a current run,
`stale_data` needs no run at all, `escalation` distinguishes a flake
from a sustained outage, and `fleet_failure` distinguishes
infrastructure problems from venue problems.

The daily digest is not redundant with the alerts — it covers the
*absence* of signal. A silent on-call channel could mean "all is
well" or "the scheduler stopped firing"; the digest distinguishes the
two.

The notifier records its dedup row *after* the delivery attempt
regardless of outcome — so a Slack outage still consumes a slot,
preventing a runaway loop of failed sends.

**Alternatives considered:**

- **Single alert table with one global cooldown.** Rejected: a
  6 h window appropriate for fresh failures would blast 4× a day
  on long-running stale-data signals.
- **No dedup, rely on Slack's "rate-limit me" UX.** Rejected: Slack
  does not coalesce alerts and the user's channel would be unreadable
  on a multi-venue outage.
- **Digest only, no per-event alerts.** Rejected: a 24 h delay on
  fresh failures is too long when the user wants to fix things during
  the same evening.
- **Per-event alerts only, no digest.** Rejected: a silent week
  cannot be distinguished from a healthy week.

**Consequences:**

- New `scraper_alerts` table with unique `alert_key` and per-row
  `last_sent_at`, `severity`, `sent_count`. Migration
  `20260425_add_scraper_alerts.py`.
- New module `backend.services.scraper_digest` plus a beat schedule
  entry in `celery_app.py` for `07:30 America/New_York`.
- New `POST /api/v1/admin/alerts/test` route fronted by a "Send test
  alert" button on the admin dashboard. Bypasses cooldown; surfaces
  which channels are configured so a missing webhook is obvious.
- Failures and successes both train the system: `_check_stale_data`
  inspects `metadata_json["created"]` over the last 7 successful
  runs, and `count_consecutive_failed_runs` walks the head of the
  history newest-first to detect sustained outages.
- Alerting infrastructure is fail-open by design — every dedup
  read/write is wrapped in try/except so a corrupt
  `scraper_alerts` row never gags real signal.

---

### 047 — Multi-Source Ticket Pricing With Provider Registry, Append-Only History, And A Shared Cooldown

**Date:** 2026-04-26
**Status:** Decided

**Decision:** Pricing is fetched from many providers per event via a
small registry of `BasePricingProvider` implementations: live APIs
(SeatGeek, Ticketmaster, TickPick search-link) and the existing
scrapers, which now also yield prices when the source page exposes
them. Quotes are persisted append-only to `ticket_pricing_snapshots`
keyed by `(event_id, source)`; the latest buy URL per source lives in
`event_pricing_links`. A nightly Celery sweep at 05:00 America/New_York
re-prices every upcoming event, and a manual `POST
/api/v1/events/<id>/refresh-pricing` endpoint lets users trigger a
sweep on demand, gated by a 5-minute cooldown stamped on
`events.prices_refreshed_at` and shared across every visitor.

**Rationale:**
The user needs as much current pricing data as possible from as many
sources as possible. A single-source price was misleading on shows
where the secondary market and the venue's primary diverged sharply,
and Tier B venues (DICE, venue-direct ticketers, Eventbrite) had no
SeatGeek presence at all so the panel was simply blank for them. The
provider abstraction lets each platform return whatever subset of the
schema it can supply (TickPick has only the URL, scraper-origin
providers have only prices, SeatGeek has the full set). Append-only
snapshots are the "data is gold" lever — historical buy/sell-side
divergence is the training set for a future buy-now prediction model;
overwriting the old row would throw that signal away.

The 5-minute cooldown is DB-backed (not Redis or in-memory) so the
"refresh just happened" state is visible to every visitor in every
tab without any cross-process coordination — the next request reads
`prices_refreshed_at` and short-circuits if it's inside the window.
The cron forces past the cooldown so it never fights the manual UI.

**Alternatives considered:**

- **Stay with SeatGeek-only (Decision 010).** Rejected: leaves Tier B
  venues blank, and a single-side quote is misleading on shows where
  primary and secondary diverge.
- **TicketsData aggregator.** Deferred per Decision 010 — pricing
  becomes a cost center long before it pays for itself; the
  per-provider registry gets us the same multi-source view for free.
- **In-memory or Redis cooldown.** Rejected: Knuckles-style multi-
  process deployments would each have their own counter, so a refresh
  in one tab wouldn't gate the others. The DB column is one read, no
  extra infrastructure.
- **Overwrite the latest pricing row instead of appending.** Rejected
  outright: kills the historical signal that motivates the whole
  feature.
- **Single Buy URL per event (the existing `events.ticket_url`).**
  Rejected: forces a one-true-link choice the user shouldn't have to
  make. The link panel renders the affiliate URL when present and
  falls back to the raw URL, per source.

**Consequences:**

- New `event_pricing_links` table and `events.prices_refreshed_at`
  column (migration `20260426_add_pricing_links_and_refresh_stamp.py`).
- New `backend.services.tickets` orchestrator owning the cooldown gate
  and the per-provider fan-out, plus `backend.services.pricing_tasks`
  for the Celery beat entry at 05:00 ET.
- `BasePricingProvider` registry in `backend.services.pricing` with
  Tier A providers (SeatGeek, Ticketmaster, TickPick) and Tier B
  scraper-origin providers fed by the existing scrapers writing
  `RawEvent.price_min/price_max` alongside their event payload.
- Frontend: `EventPricingPanel` (client) on the detail page with a
  Refresh button + cooldown banner; `PricingFreshnessBanner` (server)
  on `/events` driven by `GET /api/v1/pricing/freshness` (an indexed
  MAX over upcoming `prices_refreshed_at`).
- `affiliate_url` is preferred over `buy_url` on every Buy CTA so
  monetization can be flipped on per-source without touching the UI.
- Past events are excluded from the daily sweep, the freshness
  banner, and the per-event refresh — once a show has happened the
  price is dead inventory.

**Note:** The manual-refresh-button consequence in this entry was
reversed by Decision 048 once the free-tier upstream APIs proved
unable to return prices for most events. The architecture (provider
registry, append-only snapshots, shared cooldown) is unchanged; only
the user-facing button was withdrawn.

---

### 048 — Hide The Manual Refresh Button Until Upstream Pricing Coverage Improves

**Date:** 2026-04-26
**Status:** Decided

**Decision:** Remove the per-event "Refresh prices" button and its
"Price unavailable" copy from `EventPricingPanel`. The panel now
renders as a pure server component, shows a price line only when one
exists, and hides itself entirely when no source has a buy-link.
The backend refresh endpoint (`POST /api/v1/events/<id>/refresh-pricing`),
its cooldown gate, and the nightly Celery sweep are all retained
intact — only the user-facing affordance is gone.

**Rationale:**
After the first full multi-source sweep across 1,303 upcoming events
the actual price-coverage rate was 22 events (1.7%). The cause is
upstream and structural, not implementation: Ticketmaster's free
Discovery API returns `priceRanges: null` for almost every arena
listing (probed 10/10 DC music events live — all null), and SeatGeek's
developer-tier API returns `stats: {}` empty for every event (the
lowest_price/highest_price fields require a higher Partner-Program or
paid tier). TickPick is search-link-only by design. The result: a
visible refresh button next to "Price unavailable" on 98% of pages
made the product feel broken — pressing it could never change
anything because the data simply isn't there to fetch. Removing the
affordance until upstream coverage improves is honest. The infra
behind it is fine; the UI promise wasn't.

**Alternatives considered:**

- **Build per-show detail-page scrapers for the four DC indie venues
  (Pie Shop, Black Cat, Comet Ping Pong, generic_html).** Probed the
  listing pages of all eight venue scrapers — none surface ticket
  prices on the listing HTML (the only `$` matches on the Comet site
  are food menu prices). Extracting prices would require fetching
  each show's detail page individually, ~280+ extra HTTP requests
  per nightly run, plus brittle per-venue parsing. The yield ceiling
  is small (these venues already have buy-links), so the
  cost-to-benefit ratio doesn't justify it.
- **Keep the button but show a tooltip explaining tier limits.**
  Rejected: the explanation is the kind of detail users shouldn't
  have to read — better to remove the affordance than to add copy
  apologizing for it.
- **Wait until SeatGeek paid tier and StubHub Marketplace are wired
  up, then re-enable the button.** This is the actual plan; the
  button can come back when coverage justifies it. Tracked under
  the Deferred Decisions table below.

**Consequences:**

- `EventPricingPanel` becomes a server component (it had been
  client-only for the refresh interaction). One fewer client bundle
  on every event detail page.
- `refreshEventPricing()` and the `RefreshPricingResponse` /
  `RefreshPricingResult` types are removed from the frontend client.
  The backend endpoint and its tests are kept intact for admin
  triggers and a future re-introduction of the button.
- The "Updated X ago" line on the panel header still renders — the
  nightly sweep populates it, and surfacing freshness is still
  useful even when prices come from a single source.
- Re-introducing the button is a small change (recreate the client
  wrapper + types and flip the panel back to client-mode) once the
  upstream coverage rate makes a manual refresh meaningful.

---

### 049 — Notification Preferences Live In A Dedicated Relational Table With JSONB Pause Snapshot

**Date:** 2026-04-26
**Status:** Decided

**Decision:** Email notification preferences live in a new
`notification_preferences` table (one row per user, FK to `users`
with CASCADE) rather than expanding the existing
`users.notification_settings` JSONB blob. Sixteen typed columns cover
every per-type toggle, the digest schedule, the weekly cap, quiet
hours, and the IANA timezone. CHECK constraints lock down the integer
ranges (`digest_hour`, quiet hours) and whitelisted enums
(`digest_day_of_week`, `show_reminder_days_before`,
`max_emails_per_week`). The "pause all" affordance is implemented as
a `paused_at` timestamp plus a `paused_snapshot` JSONB column that
captures the per-type flags at pause-time so resume restores the
user's prior choices instead of forcing them to re-toggle.

**Rationale:**
Triggered email paths (artist announcements, selling-fast alerts,
show reminders) read these preferences on every fan-out. With
sixteen fields, several of them constrained to small whitelists, the
relational shape gives us indexed reads, CHECK-constraint validation
at the DB layer, and ordinary column-level migrations when fields
need to evolve. JSONB-on-`users` would have pushed all of that into
Python coercion and lost the safety net for the data layer. The
pause-snapshot keeps the schema flat (one row per user, no shadow
table) while preserving "I had selling-fast on, and I want it on
again when I un-pause."

**Alternatives considered:**

- **Expand `users.notification_settings` JSONB.** Rejected: every read
  path would have to re-validate the dict shape in Python, and the
  fields most prone to abuse (hour-of-day, weekday name) are exactly
  the ones a CHECK constraint enforces cheaply.
- **Per-type rows (one row per (user, type)).** Rejected: every email
  path would need a join to assemble the full preference picture, and
  the digest schedule + quiet hours + cap don't fit the per-type
  shape — they would have lived as orphan rows or as separate columns
  on `users` anyway.
- **Encode pause as setting every per-type flag to false.** Rejected:
  the user would lose their granular choices when un-pausing.
  Snapshotting into a JSONB column preserves them with no extra
  schema cost.

**Consequences:**

- A new Alembic migration backfills one row per existing user with
  the column defaults (actionable alerts on, discovery off, digest
  off, max 3/week, 21:00–08:00 quiet hours, America/New_York tz).
- Service layer `_validated_updates` re-validates everything in
  Python so 422s come back from the API before any DB write — the
  CHECK constraints are the second line of defense, not the first.
- The `users.notification_settings` JSONB column is left in place for
  Phase 5 (frequency-cap counters and any per-event metadata that
  shouldn't bloat the preferences table).
- Phases 2–5 of the email sprint can read preferences with a single
  indexed lookup; the digest scheduler can filter "weekly_digest=true
  AND paused_at IS NULL AND digest_day_of_week='monday'" without
  shape gymnastics.

---

### 050 — Unsubscribe Tokens Are Custom HMAC, Not JWT, And The Endpoint Is Unauthenticated

**Date:** 2026-04-26
**Status:** Decided

**Decision:** Outbound emails carry a one-click unsubscribe link
signed with a custom `header.payload.signature` HMAC-SHA256 token
(URL-safe base64, padding stripped) rather than a JWT. The
`/api/v1/unsubscribe` endpoint is intentionally unauthenticated — the
signed token in the query string IS the credential — and accepts both
GET (preview without writing) and POST (commit, including form-encoded
`List-Unsubscribe=One-Click` per RFC 8058). The signing secret comes
from `EMAIL_TOKEN_SECRET` with a fallback to `JWT_SECRET_KEY` so dev
environments don't need new secrets material. Tokens have a 90-day TTL.

**Rationale:**
The unsubscribe endpoint must work from a stale Inbox search a month
later, must accept Gmail/Apple Mail's auto-clicks (no Authorization
header is sent on those), and must never require the user to log in.
A signed self-contained token meets all three constraints and keeps
the endpoint stateless. We chose a hand-rolled HMAC over JWT to avoid
pulling PyJWT in for a one-purpose use case where we control both
sides — the wire format is ~30 lines of code and doesn't bring along
JWT's asymmetric-key footguns. RFC 8058 one-click is non-negotiable
for Gmail bulk-sender compliance starting in 2024+; the POST handler
ignores the form body and reads the token from the query string, so
the same URL works for both manual clicks and gateway auto-clicks.

**Alternatives considered:**

- **PyJWT + HS256.** Rejected: extra dependency for a single-purpose
  token format we fully control. Plain HMAC is auditable in 30 lines.
- **Server-side token table with a random opaque ID.** Rejected:
  unbounded growth (one row per outbound email × 90 days), and no
  benefit over a signed token since we only ever verify, not enumerate.
- **Require login at the unsubscribe URL.** Rejected: violates RFC 8058
  one-click, and the recipient expects the Inbox unsubscribe pill to
  "just work" without a login round-trip. Login-gating is the wrong
  user model for this affordance.
- **Bearer-auth on the endpoint.** Rejected: Gmail/Apple Mail
  auto-clickers don't send Authorization headers, and we're not
  going to ask mailbox providers to support a custom auth scheme.

**Consequences:**

- Malformed/expired tokens surface as `VALIDATION_ERROR` (HTTP 422)
  rather than 401 — the endpoint never expected an auth header.
- `compose_email()` is the canonical way to send a templated email:
  it mints the token, builds the public unsubscribe URL, injects
  `unsubscribe_url` into the template context (so the footer renders
  a clickable link), renders the HTML+text pair, and forwards to
  `send_email()` with the `List-Unsubscribe` and
  `List-Unsubscribe-Post` headers attached.
- The frontend can render a "Confirm unsubscribe" preview screen by
  GETting the same URL — the GET handler verifies the token and
  returns the user_id and scope without writing.
- Rolling the signing secret invalidates only the universe of
  in-flight links; no DB migration needed. We accept that recipients
  with very old emails (>90 days) get a "link expired" error and
  must re-request a manage-subscriptions email.

---

### 051 — Weekly Digest Dispatcher Fires Hourly And Filters In Python By User Timezone

**Date:** 2026-04-26
**Status:** Decided

**Decision:** Celery beat fires
`backend.services.notification_tasks.dispatch_weekly_digests` once per
hour at `minute=0`. The task selects every active weekly subscriber
(`weekly_digest=True AND paused_at IS NULL`) in one query, then loops
in Python and calls `is_due_for_weekly_digest(prefs, now)` and
`is_in_quiet_hours(prefs, now)` to decide whether each user is due
in the current local hour. Per-user send happens inline inside the
same task (not a fanned-out subtask).

**Rationale:** The dispatcher's filter requires comparing the user's
*local* weekday/hour to their stored `digest_day_of_week` /
`digest_hour`, but Postgres has no built-in `AT TIME ZONE` predicate
that takes a per-row IANA tz name without contortions. Doing the
filter in Python keeps the SQL trivially indexable
(`weekly_digest=true`, `paused_at IS NULL`) and lets us reuse the
same predicate functions the per-user send pipeline calls.
Inline send (instead of `chain`/`group` of subtasks) is fine at the
project's expected user count: a single worker can drain an hour's
bucket inside the 25-minute soft time limit, and Celery's broker-level
visibility timeout keeps a half-finished bucket from being replayed.

**Alternatives considered:**

- **SQL-level filter using `digest_day_of_week`/`digest_hour` as a
  composite predicate against `now() AT TIME ZONE prefs.timezone`.**
  Rejected: requires a `LATERAL` join or a generated column to handle
  per-row tz naming, and produces a query plan that is harder to
  reason about than the Python loop. The Python loop is O(N) over a
  set we're going to send to anyway.
- **Per-user fanned-out subtasks (`group(send_weekly_digest_task.s(uid)
  for uid in due)`).** Rejected for now: adds broker round-trips and
  retry surface area without buying anything at our user count. The
  per-user task still exists (`send_weekly_digest_task`) for ad-hoc
  resends, just not as the dispatcher's primary path.
- **Daily cron at 08:00 ET that sends to everyone "due that day."**
  Rejected: a Pacific user with `digest_hour=8` would get their digest
  at 05:00 PT, not 08:00 PT. The hourly cadence is what makes
  per-user `digest_hour` configurable.

**Consequences:**

- A user changing their `digest_day_of_week` or `digest_hour` takes
  effect at the next top-of-hour fire — no ad-hoc rescheduling needed.
- Cap and idempotency guards (`is_at_weekly_cap`,
  `_has_recent_weekly_log`) live inside the per-user send function and
  are re-checked on every fire, so a duplicate beat run inside the
  same hour cannot produce two emails to the same recipient.
- The dispatcher's structured log line carries six counters
  (`candidates`, `sent`, `skipped_not_due`, `skipped_quiet_hours`,
  `skipped_send_returned_false`, `errors`) so a stuck pipeline shows
  up as a counter that flat-lines, not silence.

### 052 — Recommendation Engine Powers Both Digest Ranking And `?sort=for_you`

**Date:** 2026-04-27
**Status:** Decided

**Decision:** The same `RecommendationEngine` that produces the
For-You feed also ranks the weekly digest, and the ranking is now
exposed through a new `?sort=for_you` query param on
`/api/v1/events`. The digest no longer ranks in-process — it triggers
`generate_for_user` and reads the persisted `recommendations` rows.
The `/events` endpoint reads the same rows when the caller passes
`?sort=for_you` and is authenticated. Two new scorers
(`FollowedArtistScorer`, `FollowedVenueScorer`) join
`ArtistMatchScorer` and `VenueAffinityScorer` so explicit follows
have first-class weight alongside Spotify-derived signals.

**Rationale:** Before this change, the digest implemented its own
ranking heuristic ("does the event match a tracked artist? sort it
first") and the public `/events` listing had no personalization. Two
divergent ranking paths meant adding a new signal had to be
implemented twice and could drift. Routing both surfaces through the
engine + persisted rows means a new scorer touches one file and
ships everywhere recommendations are surfaced.

**Alternatives considered:**
- *Keep the digest's local ranking and add a separate per-request
  scorer for `/events`.* Rejected — same drift problem, plus a per-
  request scorer makes pagination painful (you have to score every
  page candidate, not just the page you serve).
- *Make `/events` always return personalized ordering when a token is
  present.* Rejected — the public listing's anonymous-by-default
  contract powers SEO. An opt-in query param keeps the SSR cache key
  stable and lets logged-in users toggle.
- *Filter event rows to the user's followed artists/venues only and
  drop the score join.* Rejected — that collapses the listing for
  users with few follows. The score-based sort degrades gracefully
  (unscored events sort last by date) and pairs naturally with the
  existing `available_only` and date filters.

**Consequences:**
- The digest assembler is now an orchestrator: it triggers
  `generate_for_user`, reads persisted recs, and ranks events by
  score with a date tiebreak. The cold-start path (no recs) falls
  back to chronological order with a "connect Spotify or follow
  artists" intro.
- `events_repo.list_events` accepts `sort` and `user_id` kwargs. When
  `sort="for_you"` and `user_id` is supplied, it LEFT JOINs
  `recommendations` on `(user_id, event_id)` and orders
  `Recommendation.score DESC NULLS LAST, Event.starts_at ASC`.
  Anonymous callers requesting `for_you` silently degrade to date
  order so cached/shared URLs keep working.
- A new helper `try_get_current_user()` does best-effort token
  validation for routes that work signed-in or anonymous. Failure
  modes (missing header, bad signature, deactivated row) all degrade
  to `None` rather than 401.
- An "Advanced filters" set landed alongside the new sort:
  `day_of_week`, `time_of_day` (early/evening/late buckets in ET),
  `has_image`, `has_price`, `followed_venues_only`,
  `followed_artists_only`. The follow-based toggles intersect with
  any explicitly-passed venues/artists so the AND semantics are
  obvious.
- Match-reason dedupe in the digest is non-trivial: when both
  `ArtistMatchScorer` and `FollowedArtistScorer` fire on the same
  artist, only the artist-match chip surfaces ("You listen to X"
  beats "You follow X"). When `FollowedVenueScorer` and
  `VenueAffinityScorer` both match the same venue, the explicit-
  follow chip wins ("You follow X" beats "You've saved shows at X").

---

### 053 — In-App Feedback Stores To DB, Reuses The Existing Slack Notifier, And Routes Optional Auth Through `try_get_current_user`

**Date:** 2026-04-27
**Status:** Decided

**Decision:** Ship the beta feedback widget as a single endpoint
(`POST /api/v1/feedback`) that persists to a new `feedback` table and
fires a Slack message via the existing `backend.scraper.notifier.send_alert`
helper. The route is auth-optional via `try_get_current_user()`. When a
user is signed in, the service overrides whatever email arrives in the
form with `user.email`; the form email field is hidden in the UI for
signed-in users. Admin triage uses the existing `@require_admin` /
`X-Admin-Key` pattern with a list/resolve pair of routes plus a new
`/admin/feedback` dashboard.

**Rationale:**
- The widget needs to work for logged-out browsers (the homepage is
  fully public and SSR'd), so requiring auth would silently lose 80% of
  the signal we actually want during beta.
- We already have a Slack alerting pipeline used by the scraper failure
  path. Adding a second notifier just for feedback would duplicate the
  webhook env var, the message-formatting code, and the email-fallback
  branch. Passing `alert_key=None` to `send_alert` bypasses the
  per-key cooldown so every submission posts, which is what we want
  for a low-volume beta channel.
- Auto-filling the email from the account (and hiding the field) means
  signed-in users don't have to retype something we already know — and
  prevents them from typo-ing it. The override is server-side so a
  malicious client can't strip the user_id and submit on someone else's
  behalf.
- A dedicated `feedback` table (not a generic `events` log) gives us a
  CHECK-constrained `kind` enum and an `is_resolved` boolean we can
  actually filter on in the admin UI without hauling a JSONB column.

**Alternatives considered:**
- **Slack only, no DB row.** Rejected — we want to triage and mark
  resolved without scrolling Slack history, and we want analytics on
  feedback volume per kind over time.
- **Linear/GitHub Issues integration.** Rejected for now — too heavy
  for unstructured beta feedback. Most messages will be one-line
  reactions, not actionable tickets. Promotion to Linear can be a
  manual triage step from the admin dashboard later.
- **Require auth.** Rejected — anonymous browsers are exactly the
  cohort whose first impression matters most for a public concert
  calendar.
- **A new SLACK_FEEDBACK_WEBHOOK_URL env var.** Rejected — one Slack
  channel for ops alerts is fine during beta. If the noise becomes a
  problem we can split later by passing a `channel` arg.

**Consequences:**
- The `feedback` table is the canonical record; Slack is fire-and-forget
  best-effort. A Slack outage cannot lose a submission and cannot fail
  the request (the helper is wrapped in `try/except` and only logs).
- Anyone adding new fields to feedback must update the model, the
  migration, the repo signature, the service (validation + truncation),
  the route's JSON parsing, the serializer, and the admin dashboard's
  row rendering — the layered architecture means there's no single
  shortcut. This is intentional.
- The admin dashboard is gated behind the same `AdminKeyGate` used by
  the user/scraper dashboards, so there's no new auth surface to
  audit.
- Per-session pill dismissal lives in `sessionStorage` under
  `greenroom.feedback.dismissed`. It's intentionally not persistent —
  re-opening a tab gives the user another nudge. If that becomes
  annoying we can promote it to `localStorage` later, but the cost of
  a missed nudge is higher than the cost of a slightly noisy one
  during beta.

---

### 054 — Slack Alerts Are Routed To Three Category Channels (Ops / Digest / Feedback) With Ops As The Universal Fallback

**Date:** 2026-04-27
**Status:** Decided

**Decision:** `notifier.send_alert` takes a
`category: Literal["ops", "digest", "feedback"]` parameter (default
`"ops"`) that selects which Slack webhook URL is used. There are three
env vars — `SLACK_WEBHOOK_OPS_URL`, `SLACK_WEBHOOK_DIGEST_URL`,
`SLACK_WEBHOOK_FEEDBACK_URL`. When a category-specific URL is empty,
the ops URL is used. Routing by call site:
- `scraper/runner.py`, `scraper/validator.py`,
  `scraper/watchdogs/*` → `ops` (default).
- `services/scraper_digest.py` → `digest`.
- `services/feedback.py` → `feedback`.
- `services/admin.py::send_test_alert` fires once per category so an
  operator can confirm all three channels are wired up in one click.

**Rationale:**
- The original single-webhook design mixed three different audiences
  in one channel: ops on-call signal (every scraper failure), product
  signal (the daily digest summary), and user feedback. Each has a
  different read pattern. Ops needs to be silent when healthy so a
  red signal stands out; the digest is a steady info-level heartbeat;
  feedback is something a PM scans, not someone debugging at 3am.
- `category` as an enum on `send_alert` (not a `webhook_url` arg)
  keeps the channel decision policy-level — call sites declare *what
  kind of signal* they're sending, not *where to send it*. The
  routing table can change without touching every call site.
- Ops as universal fallback means you can ship with one webhook
  configured and everything still lands somewhere — no silent drops.
  Adding the digest and feedback channels later is a config change,
  not a code change.

**Alternatives considered:**
- **Pass `webhook_url` directly to `send_alert`.** Rejected — leaks
  the routing decision to every call site and makes a global
  re-routing impossible without grep.
- **Keep one channel, separate by Slack message prefix.** Rejected —
  notification settings, mute rules, and on-call rotations are
  per-channel in Slack. Prefix-based filtering is fragile and
  doesn't scale to multiple humans.
- **Make ops required (no empty default).** Rejected — local dev
  and tests run with all three blank, and the existing
  `if not webhook_url` guard already handles the "nothing configured"
  case gracefully. Failing config validation just to enforce one
  webhook would be a regression for anyone who doesn't run Slack
  locally.

**Consequences:**
- The old `SLACK_WEBHOOK_URL` env var is gone. Production deployments
  must rename it to `SLACK_WEBHOOK_OPS_URL` before the next release.
  The `.env.example` and Railway config need to be updated.
- The admin "test alert" button now sends three Slack messages
  instead of one. The response payload gained a `categories` dict so
  the admin dashboard can show per-channel delivery status.
- Adding a fourth category (e.g. `recommendations` for ML pipeline
  alerts) is a one-line change to the `Literal` plus a settings
  field plus an entry in `_resolve_webhook_url`. Existing call sites
  are unaffected.
- The fallback semantics (`category_url or ops_url`) mean
  `_resolve_webhook_url` can return an empty string when *nothing* is
  configured. The Slack helper guards against that with the existing
  `if not webhook_url` check, so an unconfigured deployment quietly
  no-ops the Slack send and falls through to email — same as before.

---

### 055 — Adopt The `knuckles-client` Python SDK Instead Of A Hand-Rolled HTTP Client

**Date:** 2026-04-29
**Status:** Decided

**Decision:** All Knuckles calls (magic-link, Google, Apple, passkey,
refresh, logout, JWKS-backed access-token verification) now go through
the published `knuckles-client>=0.1.0` SDK via a single
`backend.core.knuckles.get_client()` singleton. The previous custom
module (`backend.core.knuckles_client` — hand-rolled `requests`
transport, file-backed JWKS cache, bespoke exception envelope) is
deleted along with its test file.

**Rationale:**
- The SDK already encodes Decision 030's contract: `X-Client-Id` /
  `X-Client-Secret` headers, `KnucklesAuthError` with `.code`
  attributes for the refresh-token error family
  (`REFRESH_TOKEN_REUSED`, `REFRESH_TOKEN_EXPIRED`, etc.), and a
  `verify_access_token` shim built on `jwt.PyJWKClient` for in-memory
  JWKS caching. Owning a parallel implementation in Greenroom was pure
  duplication and a drift risk every time Knuckles added a code or
  changed a payload shape.
- Catching `KnucklesAuthError` and pulling `.code` off the SDK
  exception is dramatically clearer than the old `if "REUSED" in
  err.message` string-pattern checks. Frontends already consume the
  same codes, so passthrough via `auth_error_to_app_error` keeps the
  UX identical.
- `KnucklesTokenError` distinguishes expired from other failures via
  `__cause__` (the SDK preserves the underlying `jwt.PyJWTError` with
  `from exc`). That gives `verify_knuckles_token` enough fidelity to
  surface `TOKEN_EXPIRED` for silent-refresh flows without re-decoding
  the token.

**Alternatives considered:**
- **Keep the custom client and pull in the SDK only for new endpoints.**
  Rejected — every endpoint we'd skip is one we'd need to maintain a
  bespoke error-translation path for. The whole-cloth swap is the only
  way to delete `backend/core/knuckles_client.py` outright.
- **Build a thin wrapper that exposes a Greenroom-shaped facade over
  the SDK.** Rejected as premature abstraction. The SDK's shape (sub-
  clients per ceremony, dataclass returns) reads cleanly at the route
  layer; an extra facade buys nothing and forces a re-rewrite if the
  SDK adds methods.

**Consequences:**
- The `KNUCKLES_JWKS_CACHE_TTL_SECONDS` env var is gone. The SDK uses
  `jwt.PyJWKClient`'s built-in cache, which is read-mostly and bounded
  by process lifetime — there's no TTL knob to expose. Removing the
  setting is safe because no code referenced it after the swap.
- Test fixtures stub the JWKS endpoint by patching
  `jwt.PyJWKClient.fetch_data` (the SDK's underlying fetcher) instead
  of the legacy `requests.get` interception. The autouse
  `_reset_knuckles_client` fixture drops the singleton between tests so
  each case rebuilds the SDK against a clean state.
- Route-level tests now mock the SDK by patching
  `route.get_client` to return a `MagicMock`; assertions inspect
  `sdk.<sub_client>.<method>.call_args.kwargs` instead of HTTP-shaped
  bodies. This is a strictly better test surface — it exercises the
  same contract the production code calls, and breaks loudly if a
  call-site argument name drifts.
- Adding new Knuckles endpoints in the future is a one-liner per route
  (`get_client().<sub>.<method>(...)`); no new transport, exception
  type, or test scaffolding required.

---

### 058 — Genre Normalization Merges MusicBrainz And Last.fm Tags Through A Curated Mapping Into ~20 GREENROOM Canonical Labels With Confidence Scoring

**Date:** 2026-05-02
**Status:** Decided

**Decision:** A nightly normalization pass collapses each artist's
raw enrichment signals — `musicbrainz_genres`, `musicbrainz_tags`, and
`lastfm_tags` — into an ordered list of GREENROOM canonical genre
labels stored on `artists.canonical_genres` with a per-label
confidence score on `artists.genre_confidence`. The mapping from raw
tag → canonical label is a hand-curated dictionary in
`backend.services.genre_normalization.GENRE_MAPPING`, applied with
substring matching on cleaned tag strings. MusicBrainz signals are
weighted 1.5× over Last.fm signals; the union is rescaled by softmax
so the strongest matched genre lands at confidence 1.0 and the rest
fall in 0.0-1.0 relative to it. Genres below a 0.5 confidence floor
are discarded; the top five survivors are written to
`canonical_genres`. The artist-match scorer's genre fallback and the
`GET /api/v1/events?genres=` filter both read from this column going
forward — the raw scraped `events.genres` array is no longer authoritative.

**Rationale:**
- Scraped event genres are inconsistent ("indie rock" vs "indie/rock"
  vs "Indie/Rock" vs missing entirely) and short-circuit the
  canonical-label model the recommender wants to operate on.
- A curated mapping is honest about what we are doing — labeling
  artists in our taxonomy, not learning one. ~20 labels keep the
  reason-chip UI ("Because you like Indie Rock") legible and bound
  the surface area of taste preferences.
- Substring matching on tags catches the long tail of variations
  ("alternative rock" / "modern rock" / "rock" / "alternative" all
  map to Rock and/or Alternative) without an ML model or fuzzy-string
  matcher to maintain.
- MusicBrainz wins ties because its `genres` table is human-curated
  and structured ("indie rock" is one entry, not a free-form tag);
  Last.fm's user tags are noisier ("seen live", "favorite") so they
  earn a lower weight even when popular.
- Softmax rescaling produces relative confidences without a global
  threshold to tune; a soft 0.5 floor + max-of-five cap is enough to
  keep the output tight and the recommender chip list readable.

**Alternatives considered:**
- *ML-based genre classification (text or audio features).* Rejected
  for the MVP — a curated mapping is auditable, reversible, and
  ships in an afternoon. Revisit when the catalog of GREENROOM labels
  needs to grow past ~30 or when scraped tag noise overwhelms the
  curated rules.
- *Trust MusicBrainz `genres` alone, ignore Last.fm.* Rejected
  because MusicBrainz coverage falls off for newer / smaller acts;
  Last.fm fills the gap at the cost of more noise, which the lower
  weight + floor mitigate.
- *Equal MB/Last.fm weighting.* Rejected — Last.fm's free-form tags
  produced false-positive labels in spot checks ("seen live" mapping
  is dropped by the cleanup, but "british" or "8-bit" leak in if
  weighted equally).
- *Continue overlapping `events.genres` directly.* Rejected — the
  array is sparse and inconsistent across scrapers. Lifting the
  filter onto `artists.canonical_genres` lets the same curated label
  set drive both filtering and recommendation reason chips.

**Consequences:**
- A new nightly Celery task runs at 05:00 ET (one hour after the main
  scraper window) to (re)normalize artists whose enrichment is newer
  than the last normalization run.
- `GET /api/v1/events?genres=` now responds based on artist labels;
  events with no recognized artists return zero rows under any genre
  filter even if their scraped `genres` array overlaps. This is the
  intended behavior — the canonical pipeline is the source of truth.
- The recommender pre-fetches a normalized name → canonical genres
  lookup once per scoring pass; per-event scoring stays session-free.
- Substring/alias matching on Spotify user genres is gone —
  `map_tags_to_canonical` puts user-side and event-side labels in the
  same space.
- **Revisit trigger:** when `GENRE_MAPPING` exceeds ~200 entries, or
  when more than 15% of normalized artists land with empty
  `canonical_genres` despite having enrichment data, evaluate
  switching to a learned classifier.

---

### 062 — Recommendations Apply A DMV-Aware Overlay (Actionability x Time-Window x Availability) On Top Of Base Scoring

**Date:** 2026-05-03
**Status:** Decided

**Decision:** After all scorers run and their outputs combine into a
single `base` score per (user, event), the engine multiplies through
three overlays before persisting the recommendation: an
**actionability** multiplier based on the event's location relative
to the user's preferred city/region (1.00 city, 0.85 same region,
0.40 different region, 0.95 no preference), a **time-window**
multiplier on a 4-bucket curve (1.00 within 3 months, 0.85 at 3-6,
0.65 at 6-12, 0.40 beyond, 0.0 for past), and an **availability**
multiplier from a per-status table (1.00 available, 0.45 sold out,
0.0 cancelled, 0.6 postponed, 0.85 unknown). The combined
`final_score = base x actionability x time_window x availability` is
what the engine sorts and persists; events whose final score is 0
are filtered before persistence.

**Rationale:**
- *Multiplicative, not additive.* Adding an actionability bonus
  would compress the dynamic range and make weak local matches
  outrank strong regional ones. Multiplying preserves the relative
  ranking each scorer produces while letting locality / timing /
  availability shape the final order across scorers.
- *Single overlay per (user, event), not per scorer.* Applying the
  overlay inside the scorer combine loop would compound the
  multipliers (0.4^N → near-zero for any user who matches multiple
  scorers). The spec calls this out as the "subtle but important"
  failure mode and the test suite locks the order explicitly.
- *Sold-out shows are downweighted, not filtered.* Users still want
  awareness of shows they could chase via resale, waitlist signups,
  or simply to know what they missed. A 0.45 multiplier is enough
  to drop them below comparable available shows but never below a
  weak match.
- *Time-window has a flat 1.0 zone from tonight through ~3 months.*
  Most ticket purchases happen 2-12 weeks before show date. Within
  that range, ranking should be driven by taste-match strength, not
  by date — a great show 8 weeks out shouldn't lose to a mediocre
  show next week.
- *Multipliers are tunable, not learned.* The constants live at the
  top of each overlay module as named values so they can be
  adjusted without code changes elsewhere. If user feedback signals
  the recommendations feel too local, too short-term, or too
  conservative on sold-out shows, the knob to turn is the relevant
  module-level constant.

**Alternatives considered:**
- *Filter sold-out and out-of-region events outright.* Rejected for
  the reasons above — users want awareness, and a hard filter
  removes the long-tail discovery the engine produces today.
- *Apply overlays inside each scorer's `score()` method.* Rejected —
  compounds the multipliers and ties overlay tuning to scorer
  internals. The current shape lets a new scorer ship without ever
  touching the overlay code.
- *Hardcode DMV city slugs in the overlay (skip the regions table).*
  Rejected — the regions abstraction (Decision 061) is small and
  pays for itself the moment we add a second market. Without it,
  every multi-market PR has to find and update every hardcoded
  list.
- *Cache invalidation on every availability change.* Considered for
  the upstream pricing/availability writer but deferred — flagging
  affected user caches for refresh on the next read is sufficient
  for the overlay's accuracy budget. A 6-hour TTL also bounds time-
  window staleness without a per-event push path.

**Consequences:**
- Every persisted recommendation now carries `base`,
  `actionability`, `time_window`, and `availability` keys in its
  `score_breakdown` JSONB alongside the per-scorer payloads.
  Existing breakdown consumers (the For-You reasons UI, admin
  tooling) keep working because the per-scorer keys are unchanged.
- Cancelled and far-past events reaching the scoring loop drop out
  before persistence (final_score is 0). The engine's existing
  upstream filter still excludes cancelled and past events at fetch
  time; this is a defense-in-depth.
- The user's preferred-city region is resolved exactly once per
  scoring run via `regions_repo.get_region_for_city`; the overlay
  itself never queries the database. Per-event overlay computation
  stays O(1).
- **Revisit trigger:** if user feedback indicates recommendations
  feel "too local" (Richmond/Baltimore users complaining about DC
  bias) or "too short-term" (missing major tour announcements),
  retune `SAME_REGION_MULTIPLIER` upward or relax the time-window
  curve at the 6-12-month bucket. If sold-out shows feel "too
  prominent" or "too suppressed," tune `AVAILABILITY_MULTIPLIERS["sold_out"]`.

---

### 065 — Push And Email Share Triggers But Render Channel-Specific Content

**Date:** 2026-05-03
**Status:** Decided

**Decision:** A single :class:`NotificationTrigger` is dispatched
through `backend.services.notification_dispatcher`, which routes it
to push, email, or both based on a static `_CHANNEL_ROUTING` table.
Each channel has its own renderer: push payloads are short, specific,
and never hedged ("Phoebe Bridgers announced" / "Capital One Arena ·
Sat, Jun 14"); email content goes through the existing weekly-digest
renderer with HTML, plain-text, and Schema.org JSON-LD versions.
Push is rate-limited at five per user per day; email is rate-limited
by the existing `max_emails_per_week` on `NotificationPreferences`
plus a one-week idempotency window. Both channels write to the same
`notification_log` table for unified dedupe and observability.

**Rationale:**
- *Different cognitive cost.* Push interrupts; email waits. Pushing
  a five-paragraph "newly announced" digest would train users to
  swipe away. Sending a one-line "Phoebe announced" email would feel
  like a missed opportunity. Different shape per channel respects
  what each one is good at.
- *Same trigger source keeps logic in one place.* When the scraper
  ingests a new event, it raises one tour-announcement trigger per
  matched user. The dispatcher decides what happens next. Adding a
  third channel (in-app inbox, SMS, …) means adding a routing rule —
  not changing every place that produces a trigger.
- *`notification_log` as the single dedupe surface.* The unique
  constraint on `(user_id, type, dedupe_key, channel)` is the
  guarantee. A duplicate scraper run, a retried Celery task, or a
  replayed trigger all hit the same lock, and the dispatcher
  short-circuits cleanly without depending on read-then-write
  gymnastics.
- *Quiet hours queue, not drop.* A tour-announcement push that
  lands at 3 AM is queued for the user's wake hour rather than
  dropped. Time-sensitive notifications still reach the user; the
  3 AM ping never does.

**Alternatives considered:**
- *Reuse the email content as the push body.* Rejected. Push
  payloads must be under 80 characters; trying to fit a full-card
  description into a notification produces ellipsis-laden bodies
  that are worse than no body at all.
- *Single dispatcher per channel.* Rejected. Two dispatchers would
  duplicate the routing logic and divide the dedupe surface in two.
  A single entry point with channel-specific renderers keeps the
  invariants in one place.
- *Aggregate "you have N notifications today" into one push.*
  Rejected for tour announcements specifically — every artist has
  a different fan base, and lumping them together loses the urgency
  that makes the push worth interrupting for. (Aggregation may make
  sense later for selling-fast or venue-announcement notifications;
  the dispatcher's shape doesn't preclude it.)

**Consequences:**
- The dispatcher's `_CHANNEL_ROUTING` table is the single source of
  truth for "what notification types fire on what channels." Adding
  a notification type means appending to it.
- Each renderer takes a `NotificationTrigger.payload` dict and
  returns a `PushPayload | None`. Returning `None` surfaces as
  `skipped:no_renderer`, which is the right behavior for a trigger
  that's missing required keys — better than crashing the worker.
- Channel preferences are independent: a user can enable push but
  disable digest, or vice versa. The dispatcher respects each
  channel's preference column.
- **Revisit trigger:** if push notification engagement is low or
  unsubscribe rates are high, the channel routing rules need
  adjustment. The same is true if users complain about getting both
  the push and the digest item for the same event — that's a sign
  the channels feel redundant rather than complementary.

---

### 067 — Admin-Triggered Artist Catalog Hydration

**Date:** 2026-05-03
**Status:** Decided

**Decision:** Grow the artists table by letting an admin click "hydrate"
on an existing artist; new rows come from that artist's Last.fm
similarity edges. Four hard-coded controls — depth ≤ 2, similarity ≥
0.5, ≤ 5 per call, ≤ 100 per 24-hour window — bound the growth.

**Rationale:**
The catalog currently grows only when scrapers ingest events. That works
for popular artists already touring DC but leaves recommendations cold
for users whose taste has limited DMV overlap. We need a way to add
similar-but-not-yet-touring artists so the recommendation engine has
more candidates to score against, without cracking the door wide enough
for an enthusiastic admin to flood the database.

The four controls each address a specific failure mode:

- **Depth limit of 2** keeps every artist within two hops of a real
  DMV-scraped seed. Without this, hydrating hydrated artists could
  drift into entirely unrelated genres in 4–5 generations. Two hops
  preserves the "people who like artists touring DC also like…"
  semantics that gives the catalog its local relevance.
- **Daily cap of 100** respects the Last.fm enrichment rate limit:
  every new artist queues four enrichment passes (MusicBrainz,
  Last.fm tags, Last.fm similar, Spotify), and Last.fm's free tier
  allows roughly 5 requests/second. 100 new artists per day produces
  ~400 enrichment calls, which fits comfortably under the limit.
- **Minimum similarity 0.5** drops Last.fm's long tail of weak
  matches. Below 0.5 the matches are mostly genre-adjacent or
  noisy collaborative-filtering artifacts; above 0.5 the matches
  are usually meaningful.
- **Per-call cap of 5** keeps the operator in control of catalog
  shape — bulk additions go through `hydrate-bulk` which iterates
  per-source, not by widening any single call.

The audit log (`hydration_log`) records every attempt, including
blocked ones. The daily-cap math reads from there rather than from
`artists.hydration_source` so any future background hydrations
(recommendation-engine batch, etc.) do not consume the operator-facing
cap.

**Alternatives considered:**
- *Auto-hydrate during enrichment.* Rejected. Removes operator
  visibility and can produce silent catalog explosions when a
  popular artist with hundreds of similars is added.
- *Per-tenant cap instead of global cap.* No tenants; single-deployment
  app. A global cap is the right level.
- *Soft cap with a warning instead of a hard cap.* Rejected. Once we
  cross the Last.fm rate limit the enrichment backlog can take days
  to drain. A hard stop is cheaper than catching up.
- *No depth tracking, prevent hydration of "non-original" artists by
  flag.* Conceptually similar but loses the analytic value of
  knowing how many hops away each artist sits.

**Consequences:**
- The dashboard's "Most hydrated" leaderboard reads from
  `hydration_log`, not from `artists.hydration_source` — every
  attempt is captured, regardless of whether the cap clipped it.
- New artists are searchable immediately but their genres and similar
  artists populate over the next few hours as the four enrichment
  passes complete. The modal's "(enriching…)" copy sets that
  expectation explicitly.
- **Revisit trigger:** if catalog growth feels too slow, raise the
  daily cap. If irrelevant artists appear in search results, raise
  the similarity threshold or lower the depth limit. If operator
  fatigue with the modal becomes an issue, raise the per-call cap
  toward 10.

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
