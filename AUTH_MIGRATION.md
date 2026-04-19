# AUTH_MIGRATION.md — GREENROOM → Knuckles

A running log of the move from in-repo auth to the standalone Knuckles
identity service. This file is the single source of truth for the
migration; update it as phases progress.

**Status:** Phase 0 — audit. No production code has been touched yet.
The Knuckles repo scaffold exists at `/Users/garrettsooter/projects/knuckles/`
but only the core (config, database, JWT, JWKS, app factory) + tests
have been written. The auth flows are still ahead. Nothing in this
file should be interpreted as "already done" unless a later phase
explicitly says so.

> **Scope correction (2026-04-19, same day):** the original audit
> framed Spotify (and future Apple Music / Tidal) as a *connected
> service inside Knuckles*. That was wrong. **Knuckles is
> identity-only.** Music-service OAuth stays in Greenroom entirely —
> tokens live in a new Greenroom-owned `music_service_connections`
> table, OAuth routes live under `backend/api/v1/music/`, and
> Knuckles never sees a Spotify credential. This file has been edited
> in place to reflect that. The scope rule is pinned in the Knuckles
> `CLAUDE.md` and `DECISIONS.md` #001.

---

## Why this migration

Auth is being extracted from GREENROOM into a standalone Flask service
called **Knuckles** that will serve GREENROOM today and additional apps
(e.g. a planned hockey app) tomorrow. GREENROOM ends up with zero auth
code of its own — it only validates JWTs that Knuckles issues. See
DECISIONS.md entry 028 for the rationale and consequences.

---

## Part 1 — Inventory of auth-related code in GREENROOM as of 2026-04-19

Every file below either *implements* auth, *tests* auth, or *consumes*
auth state in a way Phase 2 will need to rewire. It is deliberately
verbose so the rewrite cannot miss an import.

### 1a. Backend — auth implementation (moves to Knuckles)

| Path | Purpose | LoC |
|---|---|---|
| `backend/core/auth.py` | `issue_token`, `verify_token`, `require_auth` decorator, `get_current_user`. HS256 JWT signer. | 179 |
| `backend/services/auth.py` | Magic-link + Google + Apple + WebAuthn passkey business logic; Spotify-specific upsert helpers; state-JWT minting and verifying. | 1127 |
| `backend/services/email.py` | SendGrid delivery for magic-link emails. Also the only caller of `sendgrid` in the repo — will ship in Knuckles alongside the magic-link service. | 86 |
| `backend/api/v1/auth.py` | Spotify OAuth routes: `GET /auth/spotify/start`, `POST /auth/spotify/complete`. Also holds the local `_upsert_spotify_user` helper. **Does NOT move to Knuckles** — Spotify is now framed as a Greenroom music-service connection. Rewires to `backend/api/v1/music/spotify.py` against a new `music_service_connections` table, no JWT issuance. | 254 |
| `backend/api/v1/auth_magic_link.py` | `POST /auth/magic-link/{request,verify}`. | 95 |
| `backend/api/v1/auth_google.py` | `GET /auth/google/start`, `POST /auth/google/complete`. State JWT helpers duplicated locally. | 135 |
| `backend/api/v1/auth_apple.py` | `GET /auth/apple/start`, `POST /auth/apple/complete`. State JWT helpers duplicated locally. | 138 |
| `backend/api/v1/auth_passkey.py` | `POST /auth/passkey/{register,authenticate}/{start,complete}`. | 135 |
| `backend/api/v1/auth_session.py` | `GET /auth/me`, `POST /auth/logout`. Provider-agnostic session endpoints. | 47 |
| `backend/data/repositories/users.py` | User + UserOAuthProvider CRUD. Also contains saved-events and recommendations queries (not auth — they stay in GREENROOM once the user row is stripped down). | 430 |
| `backend/data/repositories/magic_links.py` | MagicLinkToken CRUD. | 105 |
| `backend/data/repositories/passkeys.py` | PasskeyCredential CRUD. | 134 |
| `backend/data/models/users.py` | ORM models for `User`, `UserOAuthProvider`, `SavedEvent`, `MagicLinkToken`, `PasskeyCredential` plus `OAuthProvider` and `DigestFrequency` enums. Mixed ownership — see §3a. | 417 |

### 1b. Backend — auth tests (delete or move to Knuckles)

| Path |
|---|
| `backend/tests/core/test_auth.py` |
| `backend/tests/services/test_auth_magic_link.py` |
| `backend/tests/services/test_auth_google.py` |
| `backend/tests/services/test_auth_apple.py` |
| `backend/tests/services/test_auth_passkey.py` |
| `backend/tests/data/test_magic_links_repo.py` |
| `backend/tests/data/test_passkeys_repo.py` |
| `backend/tests/data/test_users_repo.py` *(partial — only the user/OAuth assertions)* |
| `backend/tests/api/v1/test_auth_routes.py` |
| `backend/tests/api/v1/test_auth_magic_link_routes.py` |
| `backend/tests/api/v1/test_auth_session_routes.py` |
| `backend/tests/api/v1/test_auth_google_routes.py` |
| `backend/tests/api/v1/test_auth_apple_routes.py` |
| `backend/tests/api/v1/test_auth_passkey_routes.py` |
| `backend/tests/api/v1/test_admin_auth.py` *(stays — admin uses a separate shared secret, not a user JWT)* |

### 1c. Backend — Alembic migrations that created auth schema

| File | What it created |
|---|---|
| `backend/migrations/versions/20260416_initial_schema.py` | `users`, `user_oauth_providers` (among many other tables). |
| `backend/migrations/versions/20260417_add_spotify_sync_to_users.py` | `users.spotify_top_artist_ids`, `spotify_top_artists`, `spotify_synced_at`. |
| `backend/migrations/versions/20260418_add_recent_artists_to_users.py` | `users.spotify_recent_artist_ids`, `spotify_recent_artists`. |
| `backend/migrations/versions/20260419_auth_identity_overhaul.py` | `users.password_hash`, `users.onboarding_completed_at`, enum values `passkey/apple_music/tidal`, tables `magic_link_tokens` + `passkey_credentials`. |

### 1d. Backend — modules that *import from* the auth surface (will be rewired)

All of these will need their imports updated in Phase 2. Most of them
import `User`, `OAuthProvider`, `users_repo`, or a helper out of
`backend.core.auth` — each site needs to be swapped for the new
"minimal local User" + the JWKS-validated request context.

| Path | What it uses |
|---|---|
| `backend/services/users.py` | `users_repo`, `User`, `DigestFrequency`. Profile CRUD for `/me`. |
| `backend/services/saved_events.py` | `users_repo`, `User`. |
| `backend/services/recommendations.py` | `users_repo`, `User`. |
| `backend/services/spotify.py` | Spotify **data sync** (top artists, recent artists) plus the Spotify OAuth round trip itself. Stays in Greenroom whole. Instead of reading tokens from `user_oauth_providers`, it reads from the new local `music_service_connections` table. No Knuckles involvement. |
| `backend/services/spotify_tasks.py` | Celery task that calls `sync_top_artists`. Stays in Greenroom; reads tokens from `music_service_connections`. |
| `backend/recommendations/engine.py` | `User`. |
| `backend/recommendations/scorers/artist_match.py` | `User.spotify_top_artist_ids`, `User.spotify_recent_artist_ids`. |
| `backend/data/models/recommendations.py` | FK to `users.id`. |
| `backend/data/models/__init__.py` | Re-exports `User`, `UserOAuthProvider`, etc. |
| `backend/api/v1/users.py` | `require_auth`, `get_current_user`. Routes: `GET /me`, `PATCH /me`, `DELETE /me`, `GET /me/top-artists`. |
| `backend/api/v1/saved_events.py` | `require_auth`, `get_current_user`. |
| `backend/api/v1/recommendations.py` | `require_auth`, `get_current_user`. |
| `backend/api/v1/admin.py` | `admin_secret_key` header, NOT user JWT — stays untouched. |

### 1e. Frontend — auth UI and context

| Path | Purpose |
|---|---|
| `frontend/src/lib/auth.tsx` | `AuthProvider`, `useAuth`, `useRequireAuth`, `login`, `logout`, `refresh`. Reads/writes `greenroom.token` in localStorage. |
| `frontend/src/lib/auth.test.tsx` | Tests for the above. |
| `frontend/src/lib/api/auth.ts` | Spotify-specific client: `startSpotifyOAuth`, `completeSpotifyOAuth`. |
| `frontend/src/lib/api/auth-identity.ts` | Magic-link, Google, Apple, passkey client functions. |
| `frontend/src/lib/webauthn.ts` | WebAuthn base64url encode/decode + browser-capability detection. Stays in GREENROOM **if and only if** Knuckles serves passkey endpoints directly to the frontend (which is the plan). |
| `frontend/src/app/login/page.tsx` | Multi-path login UI. Every button currently hits a GREENROOM endpoint. |
| `frontend/src/app/auth/verify/page.tsx` | Magic-link landing page. |
| `frontend/src/app/auth/google/callback/page.tsx` | Google return. |
| `frontend/src/app/auth/apple/callback/route.ts` | Apple form_post handler → redirect. |
| `frontend/src/app/auth/apple/callback/complete/page.tsx` | Apple finalizer. |
| `frontend/src/app/api/auth/callback/spotify/page.tsx` | Spotify return page (connect-only after Phase 1). |
| `frontend/src/app/settings/page.tsx` | "Connected services" (Spotify connect/reconnect) + "Security" (passkey register). |
| `frontend/src/components/providers/AppProviders.tsx` | Mounts `<AuthProvider>`. |
| `frontend/src/components/layout/AuthNav.tsx` + `.test.tsx` | Nav that reads `useAuth()`. |
| `frontend/src/components/events/SaveEventButton.tsx` + `.test.tsx` | Reads `useAuth()` to decide to save or bounce to `/login`. |
| `frontend/src/lib/saved-events-context.tsx` + `.test.tsx` | Reads `useAuth()` for the token. |
| `frontend/src/app/for-you/page.tsx` | Reads `useAuth()`. |
| `frontend/src/app/saved/page.tsx` | Reads `useAuth()`. |
| `frontend/src/lib/api/me.ts` | `getMe` / `updateMe` / `deleteMe`. Hits GREENROOM today; Phase 2 splits `/me` between Knuckles (profile identity) and GREENROOM (app-local preferences). |
| `frontend/src/lib/api/recommendations.ts` | Token-authenticated GREENROOM call. No change. |

### 1f. Environment + config variables to move or add

**Currently owned by GREENROOM, will move to Knuckles** (except where
noted):
- `JWT_SECRET_KEY`, `JWT_EXPIRY_SECONDS` — Knuckles (GREENROOM no longer signs).
- `MAGIC_LINK_TTL_SECONDS` — Knuckles.
- `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL` — Knuckles.
- `GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REDIRECT_URI` — Knuckles.
- `APPLE_OAUTH_CLIENT_ID / _TEAM_ID / _KEY_ID / _PRIVATE_KEY / _REDIRECT_URI` — Knuckles.
- `SPOTIFY_CLIENT_ID / _SECRET / _REDIRECT_URI` — **stays in Greenroom.** Spotify OAuth is a Greenroom music-service connection; Knuckles never sees these.
- `WEBAUTHN_RP_ID / _RP_NAME / _ORIGIN` — Knuckles.
- `FRONTEND_BASE_URL` — stays in GREENROOM (used by sitemap etc.); Knuckles needs its own copy for email links.

**New in GREENROOM for the Knuckles integration:**
- `KNUCKLES_URL` — base URL of the Knuckles deployment.
- `KNUCKLES_CLIENT_ID` — GREENROOM's `app_clients` row id in Knuckles.
- `KNUCKLES_JWKS_CACHE_PATH` — on-disk fallback for the Knuckles public
  key (Phase 3 hardening).

---

## Part 2 — What is complete vs partial vs not started

### Complete in GREENROOM today
- **Magic-link email sign-in** end-to-end: request, verify, JWT issuance, SendGrid delivery, SHA-256 hashing at rest, single-use enforcement, 15-minute TTL. Tests: 14 service tests + 6 route tests.
- **Google OAuth sign-in** end-to-end. Start, code exchange, profile upsert, JWT issuance. Tests green.
- **Sign-in-with-Apple** end-to-end including private-relay email handling and id-token verification against Apple's JWKS. Tests green.
- **WebAuthn passkey** registration + authentication ceremonies using `py_webauthn`. Sign-count monotonicity, usernameless discoverable credentials, state carried in short-lived signed JWTs. 25 new tests committed in `d0f7b25` (see commit log).
- **Spotify OAuth** as a *connected service* (post-Decision-026): `GET /auth/spotify/start`, `POST /auth/spotify/complete` still exist and still upsert `user_oauth_providers` so users who authed with Spotify before the overhaul are not broken. After Phase 2 these routes rewire to `backend/api/v1/music/spotify.py` and write into a new local `music_service_connections` table. **They do NOT move to Knuckles.**
- **Session endpoints** `/auth/me` and `/auth/logout` (`auth_session.py`).
- **Frontend multi-path login UI** wired for all four paths.
- **Settings "Connected services"** and **Settings "Security"** (passkey register) sections.

### Partial / inherited from Decision 026 but not yet shipped elsewhere
- **Onboarding genre picker (Phase 4)** — `users.onboarding_completed_at` column exists, no flow wired. Not auth-critical for the migration.
- **Apple Music / Tidal** — enum values exist in `oauth_provider` but no service code. Not auth-critical for the migration.

### Not started (and now subsumed by the Knuckles migration)
- **Refresh-token rotation.** Current JWTs are single-type, 1-hour HS256. No refresh path exists — users get signed out at expiry. This is called out explicitly because Knuckles introduces RS256 access tokens + long-lived refresh tokens, which is a protocol change, not just a move.
- **Asymmetric signing.** GREENROOM signs with a shared symmetric key. Knuckles will sign with RS256 and publish a JWKS endpoint so every downstream app validates locally without calling Knuckles on the hot path.
- **App client isolation.** Currently no notion of "this token was issued for GREENROOM" — Knuckles introduces `app_clients` and tags each token with the client id.
- **Per-user disk-cached public key fallback (Phase 3 hardening).**

---

## Part 3 — Ownership boundaries after the migration

### 3a. Tables that move out of GREENROOM into Knuckles

| Table | Notes |
|---|---|
| `magic_link_tokens` | Pure auth state — moves whole. |
| `passkey_credentials` | Pure auth state — moves whole. |
| `user_oauth_providers` | Only the `google` and `apple` rows move to Knuckles. `spotify` (and any future `apple_music` / `tidal`) rows are migrated into Greenroom's new `music_service_connections` table — not into Knuckles. Knuckles' provider enum is `{google, apple}` only. |
| `users` *(the identity columns)* | Knuckles owns `email`, `display_name`, `avatar_url`, `is_active`, `last_seen_at`, the OAuth (google/apple) relationships, and the magic-link/passkey relationships. (`password_hash` is dropped — not used by Knuckles' flows.) |

### 3b. Tables that stay in GREENROOM

| Table | Notes |
|---|---|
| `users` *(the app-local columns)* | GREENROOM keeps a narrow `users` shell: `id` (matching the Knuckles UUID), `display_name`, `avatar_url` (denormalized cache), `created_at`. Populated lazily on first valid JWT. Everything else — `email`, `is_active`, `last_login_at`, the OAuth relationships — lives in Knuckles. |
| `users.city_id`, `digest_frequency`, `genre_preferences`, `notification_settings`, `onboarding_completed_at` | **App preferences, stay in GREENROOM.** These are GREENROOM product state, not identity state. |
| `users.spotify_top_artist_ids`, `spotify_top_artists`, `spotify_recent_artist_ids`, `spotify_recent_artists`, `spotify_synced_at` | **App-local sync cache, stay in GREENROOM.** Used by the artist-match scorer. Fed by `sync_top_artists`, which reads the Spotify access token from GREENROOM's new `music_service_connections` table. Knuckles is not on this path. |
| `music_service_connections` *(new, Greenroom-only)* | New table introduced as part of this migration. Holds `user_id` (Knuckles UUID), `service` (`spotify` / `apple_music` / `tidal`), `access_token`, `refresh_token`, `token_expires_at`, `scopes`. Greenroom owns this outright. |
| `saved_events`, `recommendations` | GREENROOM product data. FK `user_id` still points at `users.id` (now a Knuckles UUID). |

### 3c. The Spotify question — resolved

**Spotify lives entirely in Greenroom.** Knuckles does not know
Spotify exists.

- **OAuth round trip** lives in Greenroom's
  `backend/api/v1/music/spotify.py` (new file, replaces the old
  `backend/api/v1/auth.py`).
- **Music data fetch and sync** (`get_top_artists`,
  `get_recently_played_artists`, `sync_top_artists`) stays in
  Greenroom's `services/spotify.py`. Instead of reading tokens from
  the deprecated `user_oauth_providers` table, it reads from the new
  local `music_service_connections` table.
- **No server-to-server handoff is needed** because no credential
  ever leaves Greenroom. This is a simpler architecture than the
  original plan and leaves Knuckles universally applicable to any
  future app.

The same pattern applies to Apple Music and Tidal when they ship —
those are Greenroom features, implemented against
`music_service_connections`, with no Knuckles involvement.

---

## Part 4 — Data migration rough plan (details come in Phase 2)

1. Stand up Knuckles with empty tables.
2. Create Greenroom's new `music_service_connections` table in a
   fresh Alembic migration.
3. For every GREENROOM user:
   - Insert a matching Knuckles `users` row with the same UUID,
     `email`, `display_name`, `avatar_url`, `is_active`,
     `last_seen_at := last_login_at`, `created_at`.
   - For each `user_oauth_providers` row:
     - If `provider` is `google` or `apple`, copy it into Knuckles.
     - If `provider` is `spotify`, **copy it into Greenroom's new
       `music_service_connections` table** (not Knuckles).
     - If `provider` is `apple_music` or `tidal` (unused at launch),
       same: route to `music_service_connections`.
   - For every `magic_link_tokens` / `passkey_credentials` row, copy
     it into Knuckles (short-lived tokens can be left to expire
     naturally — no need to migrate unused magic-link rows).
4. Cut over GREENROOM to the Knuckles JWKS.
5. Existing HS256 JWTs become invalid after the cutover because the
   signing key changes. The frontend will show a friendly "Sign in
   again" banner on the login page.
6. Drop the auth tables from the GREENROOM database (reverse the
   relevant Alembic migrations, or write a new "remove auth tables"
   migration — preferred, because Alembic downgrades that far would
   also drop product columns). Spotify columns on `users` stay; they
   are app-local sync cache, not auth state.

Risks flagged early:
- **Enum drop.** `oauth_provider` enum is used by the
  `user_oauth_providers` table only, but the enum itself has been
  extended twice. After dropping the table we can drop the enum
  cleanly.
- **FK from `saved_events` / `recommendations` to `users.id` remains
  valid** because the UUID is shared between the two databases.
- **No cross-DB foreign key.** GREENROOM cannot enforce that a
  `users.id` it sees on a JWT actually exists in Knuckles; it trusts
  the JWT. That's the intended model — JWT signature is the trust
  root.

---

## Part 5 — Operational checklist the audit leaves for the later phases

- [x] Create the Knuckles repo at `/Users/garrettsooter/projects/knuckles/` (Phase 1, in progress — scaffold + core + tests written).
- [ ] Stand up an isolated Knuckles PostgreSQL (Phase 1).
- [ ] Add Greenroom's `music_service_connections` table + migration (Phase 2, part of the same change that deletes auth tables).
- [ ] Write the cross-DB user migration script (Phase 2).
- [ ] Register GREENROOM as a Knuckles `app_client` and store the
      `KNUCKLES_CLIENT_ID` in GREENROOM's env (Phase 2).
- [ ] Cache the Knuckles JWKS to disk in GREENROOM (Phase 3).
- [ ] Verify all 380 existing GREENROOM backend tests still pass after
      the auth module deletions (Phase 2).
- [ ] Verify all frontend auth-dependent components still work against
      Knuckles URLs (Phase 2).

---

## Revision log

| Date | Phase | Change |
|---|---|---|
| 2026-04-19 | 0 | Initial audit inventory written. |
| 2026-04-19 | 0 | Scope correction: music services (Spotify / Apple Music / Tidal) stay in Greenroom entirely. Knuckles is identity-only. Part 1a, 1d, 1f, 2, 3a, 3b, 3c, 4, 5 updated in place. |
| 2026-04-19 | 2 | Phase 2 complete. Local auth deleted, `music_service_connections` renamed in place, `require_auth` auto-provisions from claims (Decision 031), identity endpoints proxied server-side via `auth_identity.py` + `knuckles_client` (Decision 032). Users table truncated in prod — no real users, auto-provision refills on next sign-in. |
