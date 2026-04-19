"""Spotify OAuth + API client.

Handles the Spotify Authorization Code flow and the thin HTTP client
wrappers the app needs at MVP: exchange an auth code for tokens, fetch
the signed-in user's profile, refresh an access token, and pull the
user's top artists for recommendation scoring.

The API-v1 routes in ``backend/api/v1/auth.py`` call these functions;
no other module talks to Spotify directly.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

from backend.core.config import get_settings
from backend.core.exceptions import SPOTIFY_AUTH_FAILED, AppError
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider, User
from backend.data.repositories import users as users_repo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Scopes the MVP needs: email for account identity, top-read for the
# artist-match scorer, recently-played for recency weighting.
DEFAULT_SCOPES: tuple[str, ...] = (
    "user-read-email",
    "user-top-read",
    "user-read-recently-played",
)

_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class SpotifyTokens:
    """OAuth tokens returned by Spotify's token endpoint.

    Attributes:
        access_token: Short-lived bearer token for the Web API.
        refresh_token: Long-lived token used to mint new access tokens.
            Spotify may omit this on a refresh response; callers should
            keep the previously stored value when it is None.
        expires_at: UTC timestamp after which ``access_token`` is invalid.
        scope: Space-separated list of granted scopes.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str


@dataclass(frozen=True)
class SpotifyProfile:
    """Snapshot of the signed-in Spotify user's profile.

    Attributes:
        id: Spotify user ID (stable external identifier).
        email: Email address on the Spotify account.
        display_name: User-facing display name, if set.
        avatar_url: URL to the user's largest available profile image.
    """

    id: str
    email: str
    display_name: str | None
    avatar_url: str | None


def build_authorize_url(*, state: str, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> str:
    """Build the Spotify consent URL the user should be redirected to.

    Args:
        state: Opaque, single-use CSRF token to round-trip through the
            consent screen and verify on callback.
        scopes: Spotify OAuth scopes to request.

    Returns:
        Fully-qualified authorize URL with query parameters populated.
    """
    settings = get_settings()
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "show_dialog": "false",
    }
    from urllib.parse import quote

    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(code: str) -> SpotifyTokens:
    """Trade an authorization code for access and refresh tokens.

    Args:
        code: Single-use code returned on the OAuth redirect.

    Returns:
        The issued :class:`SpotifyTokens`.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if Spotify rejects the exchange.
    """
    settings = get_settings()
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.spotify_redirect_uri,
        },
        headers={"Authorization": _basic_auth_header()},
        timeout=_HTTP_TIMEOUT,
    )
    return _parse_token_response(response)


def refresh_access_token(refresh_token: str) -> SpotifyTokens:
    """Obtain a fresh access token using a stored refresh token.

    Args:
        refresh_token: The refresh token previously issued to the user.

    Returns:
        A new :class:`SpotifyTokens`. ``refresh_token`` on the returned
        object may be None; callers should retain the prior value in
        that case.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if Spotify rejects the refresh.
    """
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Authorization": _basic_auth_header()},
        timeout=_HTTP_TIMEOUT,
    )
    return _parse_token_response(response)


def get_profile(access_token: str) -> SpotifyProfile:
    """Fetch the signed-in user's Spotify profile.

    Args:
        access_token: Valid Spotify access token.

    Returns:
        A :class:`SpotifyProfile` for the account.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if the profile request fails
            (e.g. expired token, revoked access).
    """
    response = requests.get(
        f"{API_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Failed to fetch Spotify profile.",
            status_code=502,
        )
    body = response.json()
    email = body.get("email")
    if not isinstance(email, str) or not email:
        # Without an email we can't key our User row — Spotify only
        # omits this when the user declined the scope. Treat as auth
        # failure so the caller shows a reconnect prompt.
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify did not return an email for this account.",
            status_code=502,
        )
    images = body.get("images") or []
    avatar_url = images[0].get("url") if images else None
    return SpotifyProfile(
        id=str(body["id"]),
        email=email,
        display_name=body.get("display_name"),
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


def get_top_artists(
    access_token: str,
    *,
    limit: int = 200,
    time_range: str = "medium_term",
) -> list[dict[str, Any]]:
    """Fetch the user's top artists from Spotify, paginating as needed.

    Spotify caps ``/me/top/artists`` at 50 items per call, so to return
    more we walk the offset in 50-item pages. Four pages covers the 200
    default (which is what the artist-match scorer trains on).

    Args:
        access_token: Valid Spotify access token.
        limit: Total number of artists to return. Values above 50 are
            fetched via multiple paginated calls. Defaults to 200.
        time_range: Window for the top calculation. One of
            ``short_term`` (~4 weeks), ``medium_term`` (~6 months),
            ``long_term`` (years). Defaults to ``medium_term``.

    Returns:
        List of raw Spotify artist dicts (id, name, genres, images, ...).

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` on HTTP failure.
    """
    page_size = min(50, max(1, limit))
    collected: list[dict[str, Any]] = []
    offset = 0
    while len(collected) < limit:
        remaining = limit - len(collected)
        params: dict[str, str | int] = {
            "limit": min(page_size, remaining),
            "offset": offset,
            "time_range": time_range,
        }
        response = requests.get(
            f"{API_BASE}/me/top/artists",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise AppError(
                code=SPOTIFY_AUTH_FAILED,
                message="Failed to fetch Spotify top artists.",
                status_code=502,
            )
        body = response.json()
        items = body.get("items") or []
        page = [item for item in items if isinstance(item, dict)]
        collected.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return collected[:limit]


def get_recently_played_artists(
    access_token: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch unique artists from the user's recently-played tracks.

    Calls ``/me/player/recently-played`` (up to 50 tracks) and flattens
    the per-track ``artists`` arrays into a de-duplicated artist list,
    preserving first-seen order so the freshest listen wins.

    Because ``/me/player/recently-played`` returns tracks (not artists),
    the returned dicts are the per-track artist objects — they have
    ``id`` and ``name`` but not ``genres`` or ``images``. Callers that
    need richer data should hydrate via ``/artists?ids=...``; today the
    scorer only needs id + name.

    Args:
        access_token: Valid Spotify access token.
        limit: Number of tracks to pull (1-50). Defaults to 50.

    Returns:
        List of distinct artist dicts in order of first appearance in
        the play history.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` on HTTP failure.
    """
    response = requests.get(
        f"{API_BASE}/me/player/recently-played",
        params={"limit": max(1, min(50, limit))},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Failed to fetch Spotify recently-played.",
            status_code=502,
        )
    body = response.json()
    items = body.get("items") or []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        track = item.get("track")
        if not isinstance(track, dict):
            continue
        for artist in track.get("artists") or []:
            if not isinstance(artist, dict):
                continue
            artist_id = artist.get("id")
            name = artist.get("name")
            dedupe_key = artist_id if isinstance(artist_id, str) and artist_id else None
            if dedupe_key is not None:
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
            elif isinstance(name, str) and name:
                if name in seen_names:
                    continue
                seen_names.add(name)
            else:
                continue
            unique.append(artist)
    return unique


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _basic_auth_header() -> str:
    """Build the HTTP Basic auth header for Spotify token requests.

    Returns:
        A ``Basic <b64>`` header value using the app's client id/secret.
    """
    settings = get_settings()
    raw = f"{settings.spotify_client_id}:{settings.spotify_client_secret}"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


# ---------------------------------------------------------------------------
# Per-user sync
# ---------------------------------------------------------------------------


def _simplify_artist(artist: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw Spotify artist payload to just the fields we display/store.

    Args:
        artist: Raw artist dict from the Spotify Web API.

    Returns:
        Slim dict with id, name, genres, and the smallest image URL that
        is large enough for a 128-pixel square (falls back to the first
        image available).
    """
    images = artist.get("images") or []
    image_url: str | None = None
    # Spotify returns images largest→smallest. Pick the smallest one
    # that is still at least 128px tall so thumbnails aren't blurry.
    for img in reversed(images):
        if isinstance(img, dict):
            height = img.get("height")
            if isinstance(height, int) and height >= 128:
                image_url = img.get("url")
                break
    if image_url is None and images:
        first = images[0]
        if isinstance(first, dict):
            image_url = first.get("url")

    return {
        "id": str(artist.get("id", "")),
        "name": artist.get("name", ""),
        "genres": [g for g in artist.get("genres", []) if isinstance(g, str)],
        "image_url": image_url if isinstance(image_url, str) else None,
    }


def _ensure_fresh_access_token(session: Session, user: User) -> tuple[str, Any] | None:
    """Return a valid Spotify access token for a user, refreshing if stale.

    Looks up the user's Spotify OAuth row, refreshes the access token
    when it is expired (or missing an expiry), persists the new tokens,
    and hands the caller a pair ``(access_token, oauth_row)``.

    Args:
        session: Active SQLAlchemy session.
        user: The user whose tokens we need.

    Returns:
        ``(access_token, oauth_row)`` when a token is available, or
        None if the user has no linked Spotify account at all.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if a refresh attempt fails.
    """
    oauth = next(
        (p for p in user.oauth_providers if p.provider == OAuthProvider.SPOTIFY),
        None,
    )
    if oauth is None or not oauth.access_token:
        return None

    now = datetime.now(UTC)
    expires_at = oauth.token_expires_at
    is_expired = expires_at is None or expires_at <= now + timedelta(seconds=30)
    if is_expired:
        if not oauth.refresh_token:
            raise AppError(
                code=SPOTIFY_AUTH_FAILED,
                message="Spotify token expired and no refresh token stored.",
                status_code=401,
            )
        tokens = refresh_access_token(oauth.refresh_token)
        users_repo.update_oauth_tokens(
            session,
            oauth,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
        )
        return tokens.access_token, oauth

    return oauth.access_token, oauth


def sync_top_artists(
    session: Session,
    user: User,
    *,
    access_token: str | None = None,
    limit: int = 200,
    recent_limit: int = 50,
) -> int:
    """Pull the user's Spotify listening snapshot and persist it.

    Syncs two parallel caches in one call:

    * Top artists (medium-term, paginated up to ``limit``) → written to
      ``spotify_top_artist_ids`` and ``spotify_top_artists``.
    * Recently-played artists (up to ``recent_limit`` tracks, flattened
      to unique artists) → written to ``spotify_recent_artist_ids`` and
      ``spotify_recent_artists``.

    Both lists feed the artist-match scorer. The "top" cap of 200 gives
    the scorer a long tail for matching against niche DMV shows; the
    "recent" list captures newly-hot artists who haven't climbed the
    6-month top yet. If ``access_token`` is omitted, the stored OAuth
    token is used (and refreshed if stale). Stamps ``spotify_synced_at``
    regardless of whether either list was non-empty.

    Args:
        session: Active SQLAlchemy session.
        user: The user to sync.
        access_token: Override token; omit to pull from the stored row.
        limit: Number of top artists to fetch (pages the API internally).
        recent_limit: Number of recently-played tracks to pull before
            flattening to unique artists.

    Returns:
        The number of top artists persisted. Recently-played counts are
        not returned because the scorer consumes both lists transparently.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` on a Spotify call failure.
    """
    token = access_token
    if token is None:
        pair = _ensure_fresh_access_token(session, user)
        if pair is None:
            return 0
        token, _oauth = pair

    raw_top = get_top_artists(token, limit=limit)
    simplified_top = [_simplify_artist(a) for a in raw_top if isinstance(a, dict)]

    try:
        raw_recent = get_recently_played_artists(token, limit=recent_limit)
    except AppError:
        # Recently-played is a nice-to-have — if Spotify flakes on that
        # endpoint we still want the top-artist sync to succeed rather
        # than fail the whole sign-in.
        logger.warning(
            "recently_played_fetch_failed",
            extra={"user_id": str(user.id)},
        )
        raw_recent = []
    simplified_recent = [_simplify_artist(a) for a in raw_recent if isinstance(a, dict)]

    user.spotify_top_artist_ids = [a["id"] for a in simplified_top if a.get("id")]
    user.spotify_top_artists = simplified_top
    user.spotify_recent_artist_ids = [a["id"] for a in simplified_recent if a.get("id")]
    user.spotify_recent_artists = simplified_recent
    user.spotify_synced_at = datetime.now(UTC)
    session.flush()
    return len(simplified_top)


def _parse_token_response(response: requests.Response) -> SpotifyTokens:
    """Decode Spotify's token endpoint response into :class:`SpotifyTokens`.

    Args:
        response: Raw ``requests.Response`` from /api/token.

    Returns:
        Parsed tokens.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if the response is non-2xx or
            missing required fields.
    """
    if response.status_code != 200:
        # Log the body so ops can debug, but never leak it to clients.
        logger.warning(
            "Spotify token endpoint returned %d: %s",
            response.status_code,
            response.text[:500],
        )
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify rejected the authentication request.",
            status_code=502,
        )
    body = response.json()
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    scope = body.get("scope", "")
    if not isinstance(access_token, str) or not isinstance(expires_in, int):
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify returned an incomplete token payload.",
            status_code=502,
        )
    refresh_token = body.get("refresh_token")
    return SpotifyTokens(
        access_token=access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        scope=scope if isinstance(scope, str) else "",
    )
