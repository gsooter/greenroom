"""Tidal OAuth + API client.

Tidal is a music-service connect (parallel to Spotify) — a user that
already holds a Knuckles session links their Tidal account so the
recommender has a second listening-history signal to score against.

The flow mirrors ``backend/services/spotify.py`` deliberately: exchange
an authorization code for tokens, fetch the connected user's profile,
refresh access tokens, and pull the listening snapshot we persist as
top-artist / recent-artist caches. Only this module talks to Tidal —
all HTTP shapes stay local so the route layer stays transport-agnostic.

Tidal v2 is JSON:API-shaped: there is no ``/me`` alias and no direct
"top artists" endpoint. We capture the caller's ``user_id`` from the
token response, fetch their profile at ``/users/{user_id}``, and build
a "top artists" proxy from ``/userCollectionArtists/{user_id}/
relationships/items`` (the user's favorited artists). Names and images
come from a second ``/artists?filter[id]=...`` hydrate call since
relationship endpoints return only identifier objects.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

from backend.core.config import get_settings
from backend.core.exceptions import TIDAL_AUTH_FAILED, AppError
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider, User
from backend.data.repositories import users as users_repo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

AUTHORIZE_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
API_BASE = "https://openapi.tidal.com/v2"

DEFAULT_SCOPES: tuple[str, ...] = (
    "user.read",
    "collection.read",
)

_HTTP_TIMEOUT = 15.0

# Tidal v2 is JSON:API — the Accept header is required on every call
# to get resource objects back; without it the gateway 404s instead of
# routing to the JSON:API handlers.
_ACCEPT_JSONAPI = "application/vnd.api+json"

# Tidal v2 requires ``countryCode`` on most resource endpoints even
# when the account is country-scoped server-side. We're DC-only today,
# so hardcode US. Plumb through settings when we go multi-market.
_DEFAULT_COUNTRY = "US"

# Page size for the user-collection relationship endpoint and batch
# size for the artist hydrate call. Tidal's ``filter[id]`` caps the
# number of ids per request; 20 is the documented safe ceiling.
_COLLECTION_PAGE_SIZE = 50
_ARTIST_HYDRATE_BATCH = 20


@dataclass(frozen=True)
class TidalTokens:
    """OAuth tokens returned by Tidal's token endpoint.

    Attributes:
        access_token: Short-lived bearer token for the OpenAPI.
        refresh_token: Long-lived token used to mint new access tokens.
            Tidal may omit this on a refresh response; callers should
            keep the previously stored value when it is None.
        expires_at: UTC timestamp after which ``access_token`` is invalid.
        scope: Space-separated list of granted scopes.
        user_id: Tidal user id echoed in the token response. Required
            for every subsequent resource call since there is no ``me``
            alias. Populated on ``authorization_code`` responses;
            refresh responses may omit it, in which case callers keep
            the previously stored value.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str
    user_id: str | None


@dataclass(frozen=True)
class TidalProfile:
    """Snapshot of the connected Tidal user's profile.

    Attributes:
        id: Tidal user ID (stable external identifier).
        email: Email address on the Tidal account. May be None — Tidal
            does not always expose email on the profile endpoint.
        display_name: User-facing display name, if set.
        avatar_url: URL to the user's profile image, if set.
    """

    id: str
    email: str | None
    display_name: str | None
    avatar_url: str | None


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``(code_verifier, code_challenge)`` pair.

    Tidal requires PKCE on the Authorization Code flow even for
    confidential clients: the ``authorize`` endpoint returns
    ``invalid_request`` when ``code_challenge`` is absent.

    Returns:
        Tuple of ``(code_verifier, code_challenge)``. The verifier is a
        43-character URL-safe random string; the challenge is the
        base64url-encoded SHA-256 digest of the verifier, stripped of
        padding, per RFC 7636.
    """
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    state: str,
    code_challenge: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    """Build the Tidal consent URL the user should be redirected to.

    Args:
        state: Opaque, single-use CSRF token to round-trip through the
            consent screen and verify on callback.
        code_challenge: PKCE S256 challenge derived from a verifier
            stored server-side for the token exchange.
        scopes: Tidal OAuth scopes to request.

    Returns:
        Fully-qualified authorize URL with query parameters populated.
    """
    settings = get_settings()
    from urllib.parse import quote

    params = {
        "client_id": settings.tidal_client_id,
        "response_type": "code",
        "redirect_uri": settings.tidal_redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(code: str, *, code_verifier: str) -> TidalTokens:
    """Trade an authorization code for access and refresh tokens.

    Args:
        code: Single-use code returned on the OAuth redirect.
        code_verifier: PKCE verifier that paired with the challenge
            sent to the authorize endpoint. Required by Tidal.

    Returns:
        The issued :class:`TidalTokens`.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if Tidal rejects the exchange.
    """
    settings = get_settings()
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.tidal_redirect_uri,
            "client_id": settings.tidal_client_id,
            "code_verifier": code_verifier,
        },
        headers={"Authorization": _basic_auth_header()},
        timeout=_HTTP_TIMEOUT,
    )
    return _parse_token_response(response)


def refresh_access_token(refresh_token: str) -> TidalTokens:
    """Obtain a fresh access token using a stored refresh token.

    Args:
        refresh_token: The refresh token previously issued to the user.

    Returns:
        A new :class:`TidalTokens`. ``refresh_token`` on the returned
        object may be None; callers should retain the prior value in
        that case.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if Tidal rejects the refresh.
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


def get_profile(access_token: str, user_id: str) -> TidalProfile:
    """Fetch a Tidal user's profile by id.

    The v2 API has no ``/users/me`` alias — callers must supply the
    ``user_id`` Tidal returned with the token response.

    Args:
        access_token: Valid Tidal access token.
        user_id: Tidal user id echoed on the token exchange.

    Returns:
        A :class:`TidalProfile` for the account.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if the profile request fails.
    """
    response = requests.get(
        f"{API_BASE}/users/{user_id}",
        params={"countryCode": _DEFAULT_COUNTRY},
        headers=_api_headers(access_token),
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        logger.warning(
            "Tidal profile fetch failed: id=%s status=%d body=%s",
            user_id,
            response.status_code,
            response.text[:500],
        )
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Failed to fetch Tidal profile.",
            status_code=502,
        )
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal returned a profile without a data envelope.",
            status_code=502,
        )
    resource_id = str(data.get("id") or user_id)
    if not resource_id:
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal returned a profile without an id.",
            status_code=502,
        )
    raw_attrs = data.get("attributes")
    attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
    email = attrs.get("email")
    display_name = (
        attrs.get("username") or attrs.get("displayName") or attrs.get("firstName")
    )
    avatar_url = attrs.get("picture") or attrs.get("avatarUrl")
    return TidalProfile(
        id=resource_id,
        email=email if isinstance(email, str) and email else None,
        display_name=display_name if isinstance(display_name, str) else None,
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


def get_top_artists(
    access_token: str, user_id: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Fetch the user's favorited Tidal artists in a single call.

    Uses the deprecated-but-still-documented ``userCollections`` shape
    (``GET /userCollections/{user_id}?include=artists``) because it is
    the only collection endpoint in Tidal's v2 surface that is keyed on
    the user id we get from the token response. The newer singular
    ``userCollectionArtists/{id}`` resource takes a separate collection
    id that isn't discoverable from a consumer-scoped token — probing
    it returns 404 regardless of scope.

    The primary resource carries
    ``data.relationships.artists.links.next`` for additional pages; we
    walk that cursor to pick up more favorites up to ``limit``.

    Args:
        access_token: Valid Tidal access token.
        user_id: Tidal user id — the collection resource is keyed by it.
        limit: Maximum number of artists to return. Defaults to 200.

    Returns:
        Artist resource dicts shaped like ``{id, type, attributes}``,
        deduplicated and capped at ``limit``.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` on HTTP failure.
    """
    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    url: str | None = f"{API_BASE}/userCollections/{user_id}"
    params: dict[str, Any] | None = {"include": "artists"}
    while url is not None and len(collected) < limit:
        response = requests.get(
            url,
            params=params,
            headers=_api_headers(access_token),
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning(
                "Tidal favorite-artists fetch failed: user=%s url=%s status=%d body=%s",
                user_id,
                response.url if hasattr(response, "url") else url,
                response.status_code,
                response.text[:500],
            )
            raise AppError(
                code=TIDAL_AUTH_FAILED,
                message="Failed to fetch Tidal favorite artists.",
                status_code=502,
            )
        body = response.json()
        for artist in _extract_artist_resources(body):
            aid = artist.get("id")
            if not isinstance(aid, str) or aid in seen_ids:
                continue
            seen_ids.add(aid)
            collected.append(artist)
            if len(collected) >= limit:
                break
        url, params = _artists_relationship_next(body)
    return collected[:limit]


def _extract_artist_resources(body: Any) -> list[dict[str, Any]]:
    """Pull artist resource objects from a ``?include=artists`` response.

    Args:
        body: Parsed JSON response body.

    Returns:
        List of artist resource dicts from the top-level ``included``
        array. An unexpected shape yields an empty list rather than
        raising so the caller falls through to an empty sync instead
        of a 502.
    """
    if not isinstance(body, dict):
        return []
    included = body.get("included")
    if not isinstance(included, list):
        return []
    return [
        item
        for item in included
        if isinstance(item, dict) and item.get("type") == "artists"
    ]


def _artists_relationship_next(
    body: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract ``data.relationships.artists.links.next`` from a response.

    Tidal paginates the sideloaded ``artists`` relationship via a
    nested next-link on the collection resource, not the top-level
    ``links.next``. Following the link reuses the query string it
    carries, so we return ``params=None`` alongside the URL.

    Args:
        body: Parsed JSON response body.

    Returns:
        ``(url, params)`` for the next page, or ``(None, None)`` when
        pagination is exhausted.
    """
    if not isinstance(body, dict):
        return None, None
    data = body.get("data")
    if not isinstance(data, dict):
        return None, None
    relationships = data.get("relationships")
    if not isinstance(relationships, dict):
        return None, None
    artists = relationships.get("artists")
    if not isinstance(artists, dict):
        return None, None
    links = artists.get("links")
    next_link = links.get("next") if isinstance(links, dict) else None
    if not isinstance(next_link, str) or not next_link:
        return None, None
    if next_link.startswith("http://") or next_link.startswith("https://"):
        return next_link, None
    from urllib.parse import urljoin

    return urljoin(API_BASE + "/", next_link.lstrip("/")), None


def _api_headers(access_token: str) -> dict[str, str]:
    """Common headers for Tidal v2 JSON:API calls.

    Args:
        access_token: Valid Tidal access token.

    Returns:
        Dict of ``Authorization`` and ``Accept`` headers.
    """
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": _ACCEPT_JSONAPI,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _basic_auth_header() -> str:
    """Build the HTTP Basic auth header for Tidal token requests.

    Returns:
        A ``Basic <b64>`` header value using the app's client id/secret.
    """
    settings = get_settings()
    raw = f"{settings.tidal_client_id}:{settings.tidal_client_secret}"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _simplify_artist(artist: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw Tidal artist payload to the fields we display/store.

    Handles both the v2 JSON:API envelope (``{id, attributes: {name,
    imageLinks: [{href, meta}, ...]}}``) and a flat fallback shape so
    one malformed response can't take down the sync.

    Args:
        artist: Raw artist dict from the Tidal OpenAPI.

    Returns:
        Slim dict with id, name, and image_url. Genres are left empty
        — v2 artist attributes do not surface genre labels.
    """
    artist_id = str(artist.get("id", ""))
    raw_attrs = artist.get("attributes")
    attrs = raw_attrs if isinstance(raw_attrs, dict) else artist

    name = attrs.get("name") or attrs.get("title") or ""
    return {
        "id": artist_id,
        "name": name if isinstance(name, str) else "",
        "genres": [],
        "image_url": _pick_image_url(attrs),
    }


def _pick_image_url(attrs: dict[str, Any]) -> str | None:
    """Pick a single artist image from Tidal's v2 ``imageLinks`` list.

    Args:
        attrs: Attribute dict from an artist resource.

    Returns:
        The largest available image URL, or None when no usable image
        is present. Falls back to ``picture`` / ``imageUrl`` scalars
        for backward compatibility with older payload shapes.
    """
    image_links = attrs.get("imageLinks")
    if isinstance(image_links, list):
        best_href: str | None = None
        best_width = -1
        for entry in image_links:
            if not isinstance(entry, dict):
                continue
            href = entry.get("href")
            if not isinstance(href, str) or not href:
                continue
            meta = entry.get("meta")
            width = -1
            if isinstance(meta, dict):
                raw_width = meta.get("width")
                if isinstance(raw_width, int):
                    width = raw_width
            if width > best_width:
                best_width = width
                best_href = href
        if best_href is not None:
            return best_href
    for key in ("picture", "imageUrl"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    return None


# ---------------------------------------------------------------------------
# Per-user sync
# ---------------------------------------------------------------------------


def _ensure_fresh_access_token(session: Session, user: User) -> tuple[str, Any] | None:
    """Return a valid Tidal access token for a user, refreshing if stale.

    Mirrors ``spotify._ensure_fresh_access_token``. Looks up the user's
    Tidal connection, refreshes the access token when it is expired
    (or missing an expiry), persists the new tokens, and returns
    ``(access_token, connection_row)``.

    Args:
        session: Active SQLAlchemy session.
        user: The user whose tokens we need.

    Returns:
        ``(access_token, connection)`` when a token is available, or
        None if the user has no linked Tidal connection.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if a refresh attempt fails.
    """
    connection = next(
        (c for c in user.music_connections if c.provider == OAuthProvider.TIDAL),
        None,
    )
    if connection is None or not connection.access_token:
        return None

    now = datetime.now(UTC)
    expires_at = connection.token_expires_at
    is_expired = expires_at is None or expires_at <= now + timedelta(seconds=30)
    if is_expired:
        if not connection.refresh_token:
            raise AppError(
                code=TIDAL_AUTH_FAILED,
                message="Tidal token expired and no refresh token stored.",
                status_code=401,
            )
        tokens = refresh_access_token(connection.refresh_token)
        users_repo.update_music_connection_tokens(
            session,
            connection,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
        )
        return tokens.access_token, connection

    return connection.access_token, connection


def sync_top_artists(
    session: Session,
    user: User,
    *,
    access_token: str | None = None,
    user_id: str | None = None,
    limit: int = 200,
) -> int:
    """Pull the user's Tidal favorite-artists snapshot and persist it.

    Writes into the Tidal-specific cache columns (``tidal_top_*``) so
    Spotify and Apple Music syncs do not clobber this user's Tidal
    artists. The artist-match scorer unions across all three caches.

    Args:
        session: Active SQLAlchemy session.
        user: The user to sync.
        access_token: Override token; omit to pull from the stored row.
        user_id: Override Tidal user id; omit to pull from the stored
            connection's ``provider_user_id``. Always required by the
            v2 API — we refuse to sync when neither source has one.
        limit: Number of favorite artists to fetch.

    Returns:
        The number of artists persisted.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` on a Tidal call failure.
    """
    token = access_token
    tidal_user_id = user_id
    if token is None or tidal_user_id is None:
        connection = next(
            (c for c in user.music_connections if c.provider == OAuthProvider.TIDAL),
            None,
        )
        if connection is None:
            return 0
        if tidal_user_id is None:
            tidal_user_id = connection.provider_user_id
        if token is None:
            pair = _ensure_fresh_access_token(session, user)
            if pair is None:
                return 0
            token, _ = pair
    if not tidal_user_id:
        return 0

    raw = get_top_artists(token, tidal_user_id, limit=limit)
    simplified = [_simplify_artist(a) for a in raw if isinstance(a, dict)]

    user.tidal_top_artist_ids = [a["id"] for a in simplified if a.get("id")]
    user.tidal_top_artists = simplified
    user.tidal_synced_at = datetime.now(UTC)
    session.flush()
    return len(simplified)


def _parse_token_response(response: requests.Response) -> TidalTokens:
    """Decode Tidal's token endpoint response into :class:`TidalTokens`.

    Args:
        response: Raw ``requests.Response`` from the token endpoint.

    Returns:
        Parsed tokens.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if the response is non-2xx or
            missing required fields.
    """
    if response.status_code != 200:
        logger.warning(
            "Tidal token endpoint returned %d: %s",
            response.status_code,
            response.text[:500],
        )
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal rejected the authentication request.",
            status_code=502,
        )
    body = response.json()
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    scope = body.get("scope", "")
    if not isinstance(access_token, str) or not isinstance(expires_in, int):
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal returned an incomplete token payload.",
            status_code=502,
        )
    refresh_token = body.get("refresh_token")
    user_id_raw = body.get("user_id")
    if isinstance(user_id_raw, str) and user_id_raw:
        user_id: str | None = user_id_raw
    elif isinstance(user_id_raw, int):
        user_id = str(user_id_raw)
    else:
        user_id = None
    return TidalTokens(
        access_token=access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        scope=scope if isinstance(scope, str) else "",
        user_id=user_id,
    )
