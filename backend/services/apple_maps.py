"""Apple Maps — MapKit JS token, Snapshot signing, and Maps Server API glue.

This module owns every Apple Maps integration Greenroom uses. The three
pieces share credentials and a common JWT-minting routine but serve
different purposes:

* :func:`mint_mapkit_token` — ES256-signed JWT handed to MapKit JS in
  the browser so it can render interactive maps. Capped at 30 minutes
  per Apple guidance. Cached in Redis for 25 minutes so every visitor
  to a venue page in the same window shares the same token.

* (Task #65) :func:`build_snapshot_url` will sign static-image URLs for
  the ``/maps/snapshot/v1/snapshot`` endpoint. Not yet implemented —
  added in the next commit.

* (Task #67) :func:`fetch_nearby_poi` will hit the Maps Server API with
  a 400m radius search. Not yet implemented.

**Runtime contract.** Every public function guards on
:func:`is_configured`; if any Apple Maps credential is missing the call
raises ``APPLE_MAPS_UNAVAILABLE`` with a 503 so route handlers can
cleanly surface "maps are off on this environment" rather than minting
tokens with a placeholder key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import jwt

from backend.core.config import get_settings
from backend.core.exceptions import APPLE_MAPS_UNAVAILABLE, AppError
from backend.core.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)

_ALGORITHM = "ES256"
# Apple caps MapKit JS tokens at 30 minutes. We mint with 30 so the
# browser-side instance has the longest possible lifetime and we cache
# the minted token for 25 minutes so every caller within one window
# shares the same cheap lookup.
_MAPKIT_TOKEN_TTL = timedelta(minutes=30)
_MAPKIT_CACHE_TTL = timedelta(minutes=25)
_MAPKIT_CACHE_KEY = "apple_maps:mapkit_token:v1"
_MAPKIT_ORIGIN_CLAIM = "origin"


def is_configured() -> bool:
    """Return True when Apple Maps credentials are fully populated.

    Returns:
        True when the team id, key id, and a private key (inline or
        on disk) are all set.
    """
    settings = get_settings()
    has_key = bool(settings.apple_mapkit_private_key) or bool(
        settings.apple_mapkit_private_key_path
    )
    return bool(
        settings.apple_mapkit_team_id and settings.apple_mapkit_key_id and has_key
    )


def mint_mapkit_token(
    *,
    origin: str | None = None,
    now: datetime | None = None,
    ttl: timedelta = _MAPKIT_TOKEN_TTL,
    redis_client: redis.Redis | None = None,
) -> dict[str, str | int]:
    """Return a MapKit JS developer token, cached for 25 minutes in Redis.

    Token shape matches Apple's `Create and Use Tokens`_ guide:

    * header: ``alg=ES256``, ``kid=<services key id>``, ``typ=JWT``
    * claims: ``iss=<team id>``, ``iat``, ``exp``, and ``origin`` when
      supplied so the token is bound to a single web origin.

    Args:
        origin: Web origin that will load MapKit JS (e.g.
            ``"https://www.greenroom.fm"``). Supplied in prod so a
            leaked token can't be reused on a different domain. Pass
            None in local dev or when serving from multiple origins.
        now: Override for the issued-at anchor, for tests.
        ttl: Token lifetime. Apple caps this at 30 minutes.
        redis_client: Optional Redis client for caching. When None the
            module's lazily-initialized client is used; when a client
            is provided but unusable, the token is minted fresh.

    Returns:
        ``{"token": str, "expires_at": int}`` where ``expires_at`` is
        a unix timestamp in seconds. Route handlers hand this straight
        to the browser.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) if credentials are
            missing; (500) if the private key fails to load.

    .. _Create and Use Tokens:
        https://developer.apple.com/documentation/mapkitjs/creating_a_maps_token
    """
    if not is_configured():
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    # Only the cache key encodes the origin — the signed payload still
    # embeds it either way. Each (origin, window) combination gets its
    # own cached token so simultaneous requests from different origins
    # don't overwrite each other.
    client = redis_client if redis_client is not None else _get_redis()
    cache_key = _build_cache_key(origin)
    cached = _read_cache(client, cache_key)
    if cached is not None:
        return cached

    anchor = now or datetime.now(UTC)
    expires_at = int((anchor + ttl).timestamp())
    token = _sign_token(origin=origin, now=anchor, ttl=ttl)
    payload = {"token": token, "expires_at": expires_at}
    _write_cache(client, cache_key, payload)
    return payload


def _sign_token(*, origin: str | None, now: datetime, ttl: timedelta) -> str:
    """Sign the MapKit JS developer token with the configured ES256 key.

    Args:
        origin: Web origin to embed as the ``origin`` claim, or None.
        now: Issued-at anchor.
        ttl: Token lifetime.

    Returns:
        Encoded JWT string.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (500) if the .p8 can't
            be loaded.
    """
    settings = get_settings()
    private_key = _load_private_key()
    claims: dict[str, str | int] = {
        "iss": settings.apple_mapkit_team_id,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if origin:
        claims[_MAPKIT_ORIGIN_CLAIM] = origin
    headers = {
        "alg": _ALGORITHM,
        "kid": settings.apple_mapkit_key_id,
        "typ": "JWT",
    }
    return jwt.encode(claims, private_key, algorithm=_ALGORITHM, headers=headers)


def _load_private_key() -> str:
    """Load the MapKit .p8 private key from env var or disk.

    Returns:
        PEM-encoded private key string.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (500) if neither source
            yields a usable key.
    """
    settings = get_settings()
    if settings.apple_mapkit_private_key:
        return settings.apple_mapkit_private_key
    path = settings.apple_mapkit_private_key_path
    if path:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AppError(
                code=APPLE_MAPS_UNAVAILABLE,
                message="Failed to load Apple Maps private key from disk.",
                status_code=500,
            ) from exc
    raise AppError(
        code=APPLE_MAPS_UNAVAILABLE,
        message="Apple Maps private key is not configured.",
        status_code=503,
    )


# ---------------------------------------------------------------------------
# Redis caching — fail-open
# ---------------------------------------------------------------------------


_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis | None:
    """Return the lazily-initialized module-level Redis client.

    Returns:
        A connected Redis client, or None if the URL is unset or a
        client could not be created. Callers treat None as a signal
        to mint fresh every call.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        url = get_settings().redis_url
    except Exception:
        logger.warning("apple_maps_redis_settings_unavailable")
        return None
    if not url:
        return None
    try:
        import redis as redis_module

        _redis_client = redis_module.Redis.from_url(url, socket_timeout=1.0)
    except Exception:
        logger.warning("apple_maps_redis_init_failed")
        return None
    return _redis_client


def reset_redis_client_for_tests() -> None:
    """Drop the cached Redis client so tests can inject their own."""
    global _redis_client
    _redis_client = None


def _build_cache_key(origin: str | None) -> str:
    """Return the Redis key for a given origin's cached token.

    Args:
        origin: The origin claim, or None for "any origin".

    Returns:
        A string Redis key.
    """
    return f"{_MAPKIT_CACHE_KEY}:{origin or '_any'}"


def _read_cache(
    client: redis.Redis | None, cache_key: str
) -> dict[str, str | int] | None:
    """Return a cached ``{token, expires_at}`` payload, if one is valid.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.

    Returns:
        The payload when present and the embedded ``expires_at`` is
        still in the future. Otherwise ``None``. All Redis errors are
        swallowed — caching is best-effort.
    """
    if client is None:
        return None
    try:
        raw = client.get(cache_key)
    except Exception:
        logger.warning("apple_maps_cache_read_failed")
        return None
    if not raw:
        return None
    try:
        import json

        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    token = parsed.get("token")
    expires_at = parsed.get("expires_at")
    if not isinstance(token, str) or not isinstance(expires_at, int):
        return None
    if expires_at <= int(datetime.now(UTC).timestamp()):
        return None
    return {"token": token, "expires_at": expires_at}


def _write_cache(
    client: redis.Redis | None,
    cache_key: str,
    payload: dict[str, str | int],
) -> None:
    """Persist a token payload in Redis with a 25-minute TTL.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.
        payload: ``{"token": ..., "expires_at": ...}`` dict.
    """
    if client is None:
        return
    try:
        import json

        client.set(
            cache_key,
            json.dumps(payload),
            ex=int(_MAPKIT_CACHE_TTL.total_seconds()),
        )
    except Exception:
        logger.warning("apple_maps_cache_write_failed")


__all__ = [
    "is_configured",
    "mint_mapkit_token",
    "reset_redis_client_for_tests",
]
