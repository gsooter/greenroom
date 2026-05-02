# DECISIONS.md — Greenroom Decision Index

One-line index of architectural decisions. Check this before making structural
changes — if something is documented here, don't reverse it without discussion.

For full rationale on any entry below, see `DECISIONS_ARCHIVE.md`.

When recording new decisions: add a single line here. Reserve full archive entries
only for decisions complex enough that future-you will genuinely need to re-read
the alternatives and reasoning months later.

---

## Stack & Infrastructure

001 — Next.js with App Router for frontend; SSR mandatory on public pages.
002 — Flask REST API backend; Celery workers share the Python process.
004 — PostgreSQL on Railway with Alembic migrations.
005 — Celery + Redis for all background jobs; nightly scrapers run at 04:00 ET.
011 — PostHog for analytics, self-hosted.
013 — Railway for backend deployment.
033 — Resend for transactional email (replaced SendGrid).
034 — Database migrations run from the prod image CMD on deploy.

## Auth (Knuckles)

026 — Greenroom is its own identity anchor; Knuckles handles all auth.
028 — Auth lives in standalone Knuckles service, not embedded in Greenroom.
029 — Music-service OAuth (Spotify, Apple Music, Tidal) stays in Greenroom; Knuckles is identity-only.
030 — Greenroom verifies Knuckles JWTs locally against cached JWKS.
031 — Greenroom users are lazily provisioned from Knuckles claims.
032 — Greenroom proxies Knuckles identity endpoints server-side.
055 — Knuckles client uses the published `knuckles-client` Python SDK.

## Data Model & Scrapers

006 — Scraper framework: BaseScraper yields RawEvent, never writes to DB.
014 — City-first data model; venues belong to cities.
015 — `event_type` enum on every event with `concert` as default.
016 — HTML scrapers parse JSON-LD first, CSS selectors only as fallback.
017 — Scraper ingestion is idempotent; re-runs produce identical results.
018 — Ticketmaster venue IDs looked up via Discovery API, never hand-entered.
043 — Dice.fm scraper uses JSON-LD parsing, not CSS selectors.
045 — Venue coverage audited against Discovery API event counts as ground truth.
046 — Scraper alerts: six independent signals with per-severity cooldowns.

## Recommendations & Personalization

007 — Recommendation engine uses strategy pattern; one scorer per signal.
008 — Browse is public; login required only for personalization.
035 — Genre overlap is a scoring fallback inside ArtistMatch, not its own scorer.
038 — Onboarding "Skip" marks a step complete without writing any data.
039 — Genre catalog is canonical on backend, fetched over HTTP.
044 — Apple Music signals: library + recently played + heavy rotation.
052 — Recommendation engine powers both digest ranking and `?sort=for_you`.
056 — MusicBrainz is the first genre enrichment source (free, community-maintained, comprehensive).

## Maps & Community

037 — Apple Maps over Google Maps for venue cartography.
040 — Community place recommendations must verify against Apple Maps.
041 — Tonight Map collapses 12 genres into 5 color buckets for readability.
036 — Venue comments use a ranked merge of hot + recent.

## Email & Notifications

049 — Notification preferences in dedicated table with JSONB pause snapshot.
050 — Unsubscribe tokens are custom HMAC, not JWT; endpoint is unauthenticated.
051 — Weekly digest dispatcher fires hourly; filters by user timezone in Python.
053 — In-app feedback stores to DB, reuses Slack notifier, optional auth.
054 — Slack alerts route to three channels (ops/digest/feedback) with ops as fallback.

## Pricing

020 — Ticketmaster `priceRanges` is unreliable; never use as primary pricing.
047 — Multi-source pricing via provider registry with append-only history and shared cooldown.
048 — Hide manual refresh button until upstream pricing coverage improves.

## SEO & Security

009 — SEO and AI discoverability are first-class features, not afterthoughts.
027 — Magic-link tokens are hashed at rest.

---

## Removed / Superseded

These entries existed but no longer apply. Their full text remains in the archive
for historical reference:

- 003 — Spotify OAuth Only — superseded by 026, 028 (Knuckles handles all auth).
- 010 — SeatGeek API as primary pricing — superseded by 047 (multi-source registry).
- 019 — DC9 Dice widget watchdog — superseded by 043 (full Dice scraper).
- 021 — Email digest deferred from MVP — now built (049–052).
- 022 — SeatGeek deferred from MVP — now built (047).
- 023 — `/track` endpoint deferred — frontend PostHog covers this.
- 024 — Feedback endpoint deferred — now built (053).
- 025 — Pre-deploy migrations — superseded by 034 (CMD-based migrations).
- 042 — Shows Near Me distance filter — implementation detail, not architectural.

---

## Deferred Decisions

Known future choices that do not need to be made yet.

| Topic | Trigger to decide |
|---|---|
| TicketsData aggregator | Cross-platform pricing parity becomes a core feature |
| React Native iOS app | Web app reaches stable active user base |
| Multi-city scraper hosting | Scraper fleet exceeds ~100 venues |
| Social features (friend activity) | Community size makes it valuable |
| Affiliate ticket links | Monetization becomes desirable |
| Full-text search engine (Elasticsearch) | PostgreSQL text search becomes a bottleneck |
| PWA push notifications | Real-time tour announcement alerts become priority |
