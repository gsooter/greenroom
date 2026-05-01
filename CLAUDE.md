# CLAUDE.md — DC Concert Aggregator

This file defines non-negotiable rules for every code change in this project.
Read it fully before writing any code. When in doubt, check here first.

---

## Project Overview

A Washington DC concert aggregator with Spotify-powered recommendations.
Venues are scraped nightly. Users sign in through the Knuckles identity
service (magic-link, Google, Apple, or passkey) and can then connect
Spotify as a music service to power recommendations. The browse
experience is fully public and server-side rendered for SEO and AI
discoverability. Personalization (recommendations, saved shows, digests)
requires login.

**Stack:**
- Frontend: Next.js (App Router) deployed on Vercel
- Backend: Flask REST API deployed on Railway
- Database: PostgreSQL (Railway managed)
- Queue: Celery + Redis (Railway, same project as Flask)
- Auth: Knuckles identity service (RS256 JWTs via JWKS); Spotify OAuth 2.0 as a music-service connect
- Email: Resend
- Analytics: PostHog (self-hosted on Railway)

---

## Absolute Rules — Never Violate These

1. **No business logic in route handlers.** Routes validate input and call
   service functions. All logic lives in the service layer.

2. **No raw SQL outside of repository functions.** All database access goes
   through repository modules in `backend/data/repositories/`.

3. **Every function has a Google-style docstring and full type hints.**
   No exceptions, including private helpers and one-liners that aren't obvious.
   See the Docstrings & Type Hints section below for the required format
   and examples. This is one of the most important rules in this file.

4. **Every public API endpoint has a corresponding pytest test.**
   Every React component with logic has a corresponding Vitest test.

5. **No hardcoded secrets, URLs, or environment-specific values in code.**
   All configuration comes from environment variables via `backend/core/config.py`
   or `frontend/src/lib/config.ts`.

6. **SSR is non-negotiable for all public-facing pages.**
   `/events`, `/events/[id]`, `/venues`, `/venues/[slug]`, and `/` must be
   server-side rendered. Never convert these to client components without
   explicit approval.

7. **Structured data (JSON-LD) is required on every event and venue page.**
   It is not optional polish — it is a core feature.

8. **Every scraper must extend `BaseScraper` and yield `RawEvent` instances.**
   No scraper writes directly to the database. No exceptions.

9. **Conventional Commits for all commit messages.**
   Format: `type(scope): description`
   Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`
   Examples:
   - `feat(scraper): add Black Cat venue scraper`
   - `fix(auth): handle expired Spotify refresh token`
   - `perf(events): add GIN index on spotify_artist_ids`

10. **No `any` type in TypeScript. No untyped Python function signatures.**
    Fix the type, don't suppress the error.

11. **Never use raw hex values in component files.**
    All colors must reference a CSS token from `globals.css`. See the
    Design System & Color Palette section below.

---

## Docstrings & Type Hints

This deserves its own section because it is non-negotiable and applies to
every single function, method, and class in the codebase — no exceptions.

### Python

Every Python function must have:
- Full type hints on all parameters and return values
- A Google-style docstring with Args, Returns, and Raises sections
- Raises section only needed if the function raises exceptions

**This applies to:** public functions, private functions, class methods,
static methods, property getters, Celery tasks, and scraper methods.
There are no exempt functions. A one-liner that seems obvious still needs
a docstring if its purpose isn't immediately clear from its name alone.

```python
def get_upcoming_events(
    city_id: UUID,
    date_from: date,
    date_to: date | None = None,
    venue_ids: list[UUID] | None = None,
    genres: list[str] | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """Fetch upcoming events for a city with optional filters.

    Args:
        city_id: UUID of the city to fetch events for.
        date_from: Start of the date range. Defaults to today.
        date_to: End of the date range. None means no upper bound.
        venue_ids: Filter to specific venues. None means all venues.
        genres: Filter to specific genres. None means all genres.
        page: Page number for pagination, 1-indexed.
        per_page: Number of results per page. Maximum 100.

    Returns:
        Tuple of (events list, total count for pagination).

    Raises:
        CityNotFoundError: If city_id does not exist.
        ValidationError: If per_page exceeds 100.
    """


def _normalize_artist_name(raw: str) -> str:
    """Strip whitespace and normalize unicode in a raw artist name string.

    Args:
        raw: The unprocessed artist name string from a scraper or API.

    Returns:
        Cleaned artist name with normalized unicode and stripped whitespace.
    """
```

### TypeScript

Every TypeScript function must have:
- Explicit parameter types — never rely on inference for function signatures
- Explicit return types — never omit the return type annotation
- A JSDoc comment for any non-trivial function

```typescript
/**
 * Formats an event date for display in the event card.
 * Returns short format for dates within the current week,
 * full format for dates further out.
 */
function formatEventDate(startsAt: Date, now: Date = new Date()): string {
  ...
}

/**
 * Fetches upcoming events for a city with optional filters.
 * Handles pagination and returns both the event list and total count.
 */
async function getEvents(
  params: EventsQueryParams
): Promise<PaginatedResponse<Event>> {
  ...
}
```

### Classes

All classes need a class-level docstring describing purpose and usage.
All TypeScript interfaces need JSDoc comments on non-obvious fields.

```python
class ArtistMatchScorer(BaseScorer):
    """Scores events based on exact artist matches in the user's Spotify history.

    Checks whether the event's headliner or any supporting act appears
    in the user's top artists or recently played artists. Returns a
    score between 0.0 and 1.0 based on match strength and recency.
    """
```

---

## Design System & Color Palette

GREENROOM uses a blush and forest green palette. These are the only colors
used in the UI. Do not introduce new colors without updating this section
and `globals.css`.

### Named Tokens

All colors are defined as CSS custom properties in `globals.css` and
referenced by name throughout the codebase. Never use raw hex values
in component files — always use the token name.

```css
/* styles/globals.css */
:root {
  /* Backgrounds */
  --color-bg-base:        #F7F0EE;  /* Petal Mist — page background */
  --color-bg-surface:     #EFE6E2;  /* Warm Linen — card surfaces, inputs */
  --color-bg-white:       #FFFFFF;  /* Pure white — raised cards */

  /* Text */
  --color-text-primary:   #1A2820;  /* Deep Canopy — headings and body */
  --color-text-secondary: #7A6A65;  /* Dusty Rose — metadata, captions */
  --color-text-inverse:   #F7F0EE;  /* On dark backgrounds */

  /* Borders */
  --color-border:         #E0D4D0;  /* Default card and input borders */

  /* Forest Green — primary actions */
  --color-green-dark:     #1E3D2A;  /* Nav bar, image placeholders */
  --color-green-primary:  #2D5A3D;  /* Buttons, CTAs, active states */
  --color-green-soft:     #C8DDD0;  /* Sage Mist — genre chips only */

  /* Blush — recommendations only */
  --color-blush-soft:     #F5D5D0;  /* Petal Pink — For You bg, saved state */
  --color-blush-accent:   #C4524A;  /* Dried Rose — save active, alerts */

  /* Navy — accent pop, used sparingly */
  --color-navy-dark:      #1E3A5A;  /* Midnight Ink — date highlights */
  --color-navy-soft:      #C8D0DC;  /* Haze Slate — secondary chips */

  /* RGB triplet companions — for rgba() tints in frosted-glass surfaces
     (sticky nav, floating heart button, etc.). Keep these in lockstep
     with the hex tokens above. */
  --color-bg-base-rgb:        247, 240, 238;
  --color-blush-soft-rgb:     245, 213, 208;
  --color-text-secondary-rgb: 122, 106, 101;
}
```

### Badge & Chip Rules

Color is used intentionally. Misusing badge colors is a bug, not a style choice.

| Badge type | Background token | Text color | When to use |
|---|---|---|---|
| For You | `--color-blush-soft` | `#7A3028` | Spotify recommendation match **only** |
| Genre | `--color-green-soft` | `#1A3D28` | Genre labels **only** |
| Neutral | `--color-bg-surface` | `--color-text-secondary` | Everything else |

**The rule in plain English:**
- Blush means "Spotify matched this for you." Use it nowhere else.
- Green means genre. Use it nowhere else.
- Everything else — sold out, going fast, available, venue name — gets neutral.

### Tailwind Config

The palette is mapped in `tailwind.config.ts` so tokens are available as
utility classes (`bg-green-primary`, `text-blush-accent`, etc.).
Never use Tailwind's default color palette — only the custom tokens.

---

## Backend Architecture

```
backend/
├── core/                        # Cross-cutting concerns
│   ├── config.py                # All env vars loaded here, nowhere else
│   ├── database.py              # SQLAlchemy engine + session factory
│   ├── auth.py                  # JWT creation, validation, decorators
│   ├── exceptions.py            # Custom exception classes
│   └── logging.py               # Structured logging setup
│
├── data/                        # Data access layer
│   ├── models/                  # SQLAlchemy ORM models, one file per table group
│   └── repositories/            # All DB queries, one file per domain
│       ├── events.py
│       ├── venues.py
│       ├── users.py
│       └── ...
│
├── services/                    # Business logic layer
│   ├── events.py                # Event ingestion, search, filtering
│   ├── recommendations.py       # Recommendation engine orchestration
│   ├── spotify.py               # Spotify API client + data sync
│   ├── tickets.py               # SeatGeek + StubHub pricing
│   ├── notifications.py         # Email digest assembly + sending
│   └── ...
│
├── api/                         # Flask route handlers — thin only
│   └── v1/
│       ├── __init__.py
│       ├── auth.py
│       ├── events.py
│       ├── venues.py
│       ├── users.py
│       ├── recommendations.py
│       ├── feedback.py
│       ├── track.py
│       └── admin.py
│
├── scraper/                     # Scraper framework
│   ├── base/
│   │   ├── scraper.py           # BaseScraper abstract class
│   │   └── models.py            # RawEvent dataclass
│   ├── platforms/               # One file per ticketing platform
│   │   ├── ticketmaster.py
│   │   ├── dice.py
│   │   ├── eventbrite.py
│   │   ├── seatgeek.py
│   │   └── generic_html.py
│   ├── venues/                  # Custom scrapers for non-standard venues
│   │   ├── black_cat.py
│   │   └── ...
│   ├── config/
│   │   └── venues.py            # Master venue → scraper mapping
│   ├── validator.py             # Post-scrape validation + alerting
│   ├── notifier.py              # Slack + email alerts
│   └── runner.py                # Celery tasks
│
└── recommendations/             # Recommendation engine
    ├── engine.py                # Orchestrator — runs all scorers
    ├── base.py                  # BaseScorer abstract class
    └── scorers/
        ├── artist_match.py
        └── similar_artist.py
```

### Layer Rules

| Layer | Can import from | Cannot import from |
|---|---|---|
| `api/` | `services/`, `core/` | `data/` directly |
| `services/` | `data/`, `core/` | `api/` |
| `data/` | `core/` | `services/`, `api/` |
| `scraper/` | `data/`, `core/` | `api/`, `services/` |
| `recommendations/` | `data/`, `core/` | `api/` |

Violating the import hierarchy is never acceptable.

### Python Standards

- **Formatter:** Black, line length 88
- **Linter:** Ruff
- **Type checker:** mypy in strict mode
- **Test framework:** pytest with pytest-cov
- **Minimum test coverage:** 80% across all backend modules
- **Python version:** 3.12+
- **Docstring style:** Google — see Docstrings & Type Hints section above

---

## Frontend Architecture

```
frontend/
├── src/
│   ├── app/                     # Next.js App Router pages
│   │   ├── page.tsx             # / — Home (SSR)
│   │   ├── events/
│   │   │   ├── page.tsx         # /events — Browse (SSR)
│   │   │   └── [id]/
│   │   │       └── page.tsx     # /events/[id] — Detail (SSR)
│   │   ├── venues/
│   │   │   ├── page.tsx         # /venues — Directory (SSR)
│   │   │   └── [slug]/
│   │   │       └── page.tsx     # /venues/[slug] — Venue (SSR)
│   │   ├── for-you/
│   │   │   └── page.tsx         # /for-you — Auth required (CSR)
│   │   ├── saved/
│   │   │   └── page.tsx         # /saved — Auth required (CSR)
│   │   ├── settings/
│   │   │   └── page.tsx         # /settings — Auth required (CSR)
│   │   ├── login/
│   │   │   └── page.tsx         # /login
│   │   ├── layout.tsx           # Root layout — AppShell
│   │   ├── sitemap.ts           # Dynamic sitemap generation
│   │   └── robots.ts            # robots.txt generation
│   │
│   ├── components/
│   │   ├── events/
│   │   │   ├── EventCard.tsx
│   │   │   ├── EventCard.test.tsx
│   │   │   ├── AgendaView.tsx
│   │   │   ├── CalendarView.tsx
│   │   │   └── ...
│   │   ├── venues/
│   │   ├── recommendations/
│   │   ├── layout/
│   │   │   ├── TopNav.tsx
│   │   │   ├── MobileBottomNav.tsx
│   │   │   └── AppShell.tsx
│   │   ├── seo/
│   │   │   ├── EventStructuredData.tsx
│   │   │   ├── VenueStructuredData.tsx
│   │   │   └── BreadcrumbStructuredData.tsx
│   │   └── ui/                  # Generic reusable primitives
│   │       ├── Modal.tsx
│   │       ├── LoadingSkeleton.tsx
│   │       ├── EmptyState.tsx
│   │       └── ...
│   │
│   ├── lib/
│   │   ├── api/                 # Typed API client functions
│   │   │   ├── events.ts
│   │   │   ├── venues.ts
│   │   │   └── ...
│   │   ├── config.ts            # Env vars, never inline
│   │   ├── auth.ts              # AuthContext + hooks
│   │   └── metadata.ts          # generateMetadata helpers
│   │
│   ├── hooks/                   # Custom React hooks
│   ├── types/                   # Shared TypeScript types
│   └── styles/
│       └── globals.css          # All color tokens defined here
│
├── public/
│   └── llms.txt                 # AI crawler discoverability file
```

### Frontend Standards

- **Framework:** Next.js 14+ with App Router
- **Language:** TypeScript strict mode, no `any`
- **Styling:** Tailwind CSS — custom tokens only, never default palette
- **Data fetching:** TanStack Query for client-side, native fetch in server components
- **State:** AuthContext for auth, local useState for UI, TanStack Query for server state
- **Test framework:** Vitest + React Testing Library
- **Linter/Formatter:** ESLint + Prettier
- **Component style:** Functional components only, no class components

---

## SEO & AI Discoverability — Priority Feature

This is a first-class feature, not an afterthought. The following requirements
are mandatory on every relevant page.

### Server-Side Rendering

All public pages (`/`, `/events`, `/events/[id]`, `/venues`, `/venues/[slug]`)
must be server components that fetch data before render. No public page may
rely solely on client-side data fetching for its primary content.

### generateMetadata — Required on Every Page

Every page must export a `generateMetadata` function. Static pages get static
metadata. Dynamic pages generate metadata from fetched data.

```typescript
// Required pattern for event detail pages
export async function generateMetadata(
  { params }: { params: { id: string } }
): Promise<Metadata> {
  const event = await getEvent(params.id);
  return {
    title: `${event.headliner} at ${event.venue.name} — ${formatDate(event.startsAt)}`,
    description: `${event.headliner} live at ${event.venue.name} in Washington DC on ${formatDate(event.startsAt)}. Tickets from $${event.minTicketPrice}.`,
    openGraph: {
      title: `${event.headliner} at ${event.venue.name}`,
      description: `${formatDate(event.startsAt)} · Washington DC · From $${event.minTicketPrice}`,
      images: [event.imageUrl],
      type: 'website',
    },
    twitter: {
      card: 'summary_large_image',
      title: `${event.headliner} at ${event.venue.name}`,
      description: `${formatDate(event.startsAt)} · Washington DC`,
      images: [event.imageUrl],
    },
  };
}
```

### Structured Data — Required on Event and Venue Pages

Every event detail page must render `<EventStructuredData />`.
Every venue page must render `<VenueStructuredData />`.
Every page must render `<BreadcrumbStructuredData />`.

```typescript
// components/seo/EventStructuredData.tsx
// Renders a <script type="application/ld+json"> block with full
// MusicEvent schema including performer, location, offers, and image.
// This is what drives Google rich results for event searches.
```

Required schema types:
- Event pages: `MusicEvent` + `BreadcrumbList`
- Venue pages: `MusicVenue` + `BreadcrumbList`
- Home page: `WebSite` + `Organization`

### Sitemap — Dynamic and Always Current

`src/app/sitemap.ts` must generate entries for every event and venue page.
It fetches from the database at build time (or ISR). Google and AI crawlers
use this to discover all indexable content.

```typescript
// Must include:
// - / (home)
// - /events
// - /events/[id] for every upcoming event
// - /venues
// - /venues/[slug] for every active venue
// Priority: event detail pages 0.9, browse 0.8, venue pages 0.8, home 1.0
// changeFrequency: event pages 'daily', others 'weekly'
```

### robots.ts — Explicitly Welcome AI Crawlers

```typescript
// src/app/robots.ts
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: '*', allow: '/' },
      { userAgent: 'GPTBot', allow: '/' },
      { userAgent: 'ClaudeBot', allow: '/' },
      { userAgent: 'PerplexityBot', allow: '/' },
      { userAgent: 'GoogleBot', allow: '/' },
    ],
    sitemap: `${process.env.NEXT_PUBLIC_BASE_URL}/sitemap.xml`,
  };
}
```

### llms.txt — AI Discoverability File

`public/llms.txt` must exist and be kept current. This is the emerging standard
for helping AI systems understand your site's content and purpose.

```
# [AppName]

> Washington DC's concert calendar with Spotify-powered recommendations.
> Aggregates shows from all major DC venues nightly.

## What this site contains

- Upcoming concerts and live music events in Washington DC
- Full event details: dates, venues, artists, ticket prices, availability
- Venue directory for DC music venues
- Personalized recommendations via Spotify listening history

## Key pages

- /events — Full DC concert calendar with filters
- /venues — Directory of DC music venues
- /events/[id] — Individual event pages with full details and pricing
- /api/v1/feed/events — Machine-readable plain text event feed

## Data freshness

Event listings updated nightly at 4am ET from venue websites and Ticketmaster.
Ticket pricing updated every 6 hours from SeatGeek.

## DC Venues Covered

9:30 Club, The Anthem, Black Cat, DC9, Comet Ping Pong,
Merriweather Post Pavilion, Echostage, Flash, Pie Shop, and more.
```

### AI-Readable Event Feed — Required Backend Endpoint

`GET /api/v1/feed/events` must exist and return a plain text response
optimized for AI crawler consumption. No authentication required.

```
Washington DC Concerts — Updated [timestamp]

TONIGHT
• [Artist] @ [Venue] — Doors [time] — From $[price] — [availability]

THIS WEEK
• [Date]: [Artist] @ [Venue] — $[price] — [availability]
...
```

This endpoint is what gets cited when someone asks an AI chatbot
"what concerts are happening in DC this week."

---

## Scraper Rules

- Every scraper extends `BaseScraper` and implements `scrape() -> Iterator[RawEvent]`
- Every scraper is registered in `scraper/config/venues.py` — this is the only
  place venue-to-scraper mapping is defined
- Scrapers never write to the database directly
- Scrapers must handle rate limiting with exponential backoff
- Scrapers must store the full original payload in `RawEvent.raw_data`
- If a scraper returns zero results, the validator fires an alert automatically
- If a scraper's event count drops below 40% of its 30-run average, a warning fires

**Adding a new venue:**
1. Add venue row to database seed / migration
2. Add entry to `scraper/config/venues.py`
3. If the venue uses an existing platform scraper, no new code needed
4. If custom logic is required, add `scraper/venues/<slug>.py`
5. Update `public/llms.txt` venue list

---

## Recommendation Engine Rules

- Every scoring strategy extends `BaseScorer`
- `RecommendationEngine` in `recommendations/engine.py` accepts a list of scorers
- Scores are summed and normalized to 0.0–1.0
- Every recommendation stores its `score_breakdown` JSONB so reasons
  can be shown to users and analyzed by developers
- Adding a new scorer never requires changes to existing scorers or the engine

---

## Environment Variables

All env vars are defined and validated in `backend/core/config.py` using Pydantic
Settings. The app fails loudly at startup if a required variable is missing.

Required variables (never hardcode these):
```
# Knuckles (identity service)
KNUCKLES_URL
KNUCKLES_CLIENT_ID
KNUCKLES_CLIENT_SECRET

# Spotify (music-service connect)
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI
SPOTIFY_BETA_EMAILS           # Comma-separated allowlist of email addresses
                              # approved for Spotify's dev-mode beta. The
                              # Spotify app is capped at 25 OAuth users; only
                              # listed addresses see a working Connect button
                              # on /settings — everyone else sees a disabled
                              # "Limited access" card. Whitespace and case
                              # are ignored.

# Tidal (music-service connect — Phase 5)
TIDAL_CLIENT_ID
TIDAL_CLIENT_SECRET
TIDAL_REDIRECT_URI

# Apple Music (music-service connect — Phase 5)
APPLE_MUSIC_TEAM_ID
APPLE_MUSIC_KEY_ID
APPLE_MUSIC_PRIVATE_KEY           # PEM-encoded .p8 contents (preferred)
APPLE_MUSIC_PRIVATE_KEY_PATH      # dev convenience, loads from disk
APPLE_MUSIC_BUNDLE_ID

# Apple Maps (MapKit JS + Snapshot + Maps Server API)
# Prefix is APPLE_MAPKIT_ — not APPLE_MAPS_. Do not create aliases.
APPLE_MAPKIT_TEAM_ID
APPLE_MAPKIT_KEY_ID
APPLE_MAPKIT_PRIVATE_KEY          # PEM-encoded .p8 contents (preferred)
APPLE_MAPKIT_PRIVATE_KEY_PATH     # dev convenience, loads from disk

# Database
DATABASE_URL

# Redis
REDIS_URL

# JWT (signing the short-lived Spotify OAuth state token)
JWT_SECRET_KEY

# Resend
RESEND_API_KEY
RESEND_FROM_EMAIL

# Ticketmaster
TICKETMASTER_API_KEY

# SeatGeek
SEATGEEK_CLIENT_ID
SEATGEEK_CLIENT_SECRET

# Admin
ADMIN_SECRET_KEY              # For /api/v1/admin/* routes

# Alerting — three Slack channels, one webhook each.
# Categories without their own webhook fall back to the ops URL,
# so a single-webhook deployment still receives every alert.
SLACK_WEBHOOK_OPS_URL         # Ops channel: scraper failures, validator alerts,
                              # watchdogs, sustained outages, fleet failures, and
                              # the admin "test alert" button. Universal fallback.
SLACK_WEBHOOK_DIGEST_URL      # Daily scraper-fleet digest channel.
SLACK_WEBHOOK_FEEDBACK_URL    # User feedback submissions channel.
ALERT_EMAIL                   # Scraper failure fallback email

# PostHog
POSTHOG_API_KEY
POSTHOG_HOST

# Sentry — error reporting for the Flask app and Celery workers.
# Empty in dev: when SENTRY_DSN is unset the SDK is never initialized
# and reporting is a no-op, so contributors don't need a Sentry account
# to run the app.
SENTRY_DSN
SENTRY_ENVIRONMENT            # production | staging | development (default)
SENTRY_TRACES_SAMPLE_RATE     # 0.0–1.0; default 0.0 (errors only)

# Frontend
NEXT_PUBLIC_API_URL
NEXT_PUBLIC_BASE_URL
NEXT_PUBLIC_POSTHOG_KEY
NEXT_PUBLIC_SENTRY_DSN              # Empty in dev disables the SDK entirely
NEXT_PUBLIC_SENTRY_ENVIRONMENT      # production | staging | development
SENTRY_ORG                          # Used by withSentryConfig at build time
SENTRY_PROJECT                      # Used by withSentryConfig at build time
```

---

## Testing Standards

**Minimum coverage: 80% across all backend modules and all frontend
components that contain logic. CI blocks merge if this threshold is not met.**

### Backend
```bash
pytest                         # Run all tests
pytest --cov=backend           # With coverage report
pytest --cov=backend --cov-fail-under=80   # Fails if below 80%
pytest -k "test_events"        # Run specific tests
```

- Tests live in `backend/tests/` mirroring the source structure
- Fixtures in `conftest.py` — shared DB session, test client, mock Spotify
- Never test implementation details — test behavior and outcomes
- Mock all external APIs (Spotify, Ticketmaster, SeatGeek) in all tests
- 80% applies to: `services/`, `data/repositories/`, `scraper/`,
  `recommendations/`, and `api/` — no module is exempt
- CI runs `pytest --cov=backend --cov-fail-under=80` on every PR

### Frontend
```bash
npx vitest                     # Run all tests
npx vitest --coverage          # With coverage report
```

- Tests co-located with components: `EventCard.tsx` → `EventCard.test.tsx`
- Use React Testing Library — test what users see, not internals
- Mock API calls with MSW (Mock Service Worker)
- 80% applies to all components and hooks that contain logic
- Pure presentational components with no logic are exempt
- CI runs coverage check on every PR and blocks merge if below 80%

---

## Database Rules

- All schema changes via Alembic migrations — never edit tables directly
- Migration files are named descriptively:
  `20240416_add_spotify_artist_ids_to_events.py`
- Every migration is reversible — always implement `downgrade()`
- GIN indexes required on all PostgreSQL array columns that are queried
- Foreign keys always have explicit `ON DELETE` behavior defined

---

## API Response Standards

### Success
```json
{
  "data": {},
  "meta": {}
}
```

### Paginated
```json
{
  "data": [],
  "meta": {
    "total": 142,
    "page": 1,
    "per_page": 20,
    "has_next": true
  }
}
```

### Error
```json
{
  "error": {
    "code": "EVENT_NOT_FOUND",
    "message": "No event found with id abc123"
  }
}
```

All error codes are constants defined in `backend/core/exceptions.py`.
Never return raw exception messages to the client.

---

## Git Workflow

- `main` — production, deploys automatically to Railway + Vercel
- `staging` — staging environment, deploy before merging to main
- `feat/*` — feature branches, branch from and PR back to `staging`
- No direct commits to `main`
- PRs require passing CI (tests + linting) before merge

---

## What Claude Code Should Always Do

- Read this file before starting any task
- Check `DECISIONS.md` before making architectural choices
- Run the linter and tests before considering a task complete
- Ask before introducing a new dependency
- Never modify the database schema without a migration file
- Never skip docstrings or type hints — see the Docstrings & Type Hints
  section above, this is a hard rule not a suggestion
- Never use raw hex values in component files — always use color tokens
- When adding a new venue scraper, update `llms.txt` too
- When adding a new page, ensure SSR, metadata, and structured data are in place

---

## Keeping These Files Current

`CLAUDE.md` and `DECISIONS.md` are living documents. Claude Code must keep
them accurate as the project evolves. Stale documentation is worse than
no documentation.

**Update `CLAUDE.md` when:**
- A new layer, module, or directory is added to the project structure
- A new tool, framework, or library becomes a project standard
- A rule or convention changes
- A new environment variable is required
- The build, test, or deployment process changes
- A new color token is introduced to the palette

**Update `DECISIONS.md` when:**
- A significant architectural choice is made during implementation
- A decision documented here is reversed or modified — update the existing
  entry's status to `Superseded` and add a new entry explaining the change
- A deferred decision from the deferred table gets resolved — move it to
  the main log with full rationale
- A new tradeoff is discovered during implementation that future developers
  should understand

**How to add a new DECISIONS.md entry:**
Use the next sequential number and follow the existing format exactly:
```
### 0XX — Short Title

**Date:** YYYY-MM-DD
**Status:** Decided | Superseded | Deferred

**Decision:** One sentence stating what was decided.

**Rationale:** Why this choice was made.

**Alternatives considered:** What else was evaluated and why it was rejected.

**Consequences:** What this decision affects or constrains going forward.
```

Do not wait until the end of a task to update these files. Update them
at the point the decision is made or the change is introduced.
