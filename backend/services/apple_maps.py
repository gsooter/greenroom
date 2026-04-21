"""Apple Maps — MapKit JS token, Snapshot signing, and Maps Server API glue.

This module owns every Apple Maps integration Greenroom uses. The three
pieces share credentials and a common JWT-minting routine but serve
different purposes:

* :func:`mint_mapkit_token` — ES256-signed JWT handed to MapKit JS in
  the browser so it can render interactive maps. Capped at 30 minutes
  per Apple guidance. Cached in Redis for 25 minutes so every visitor
  to a venue page in the same window shares the same token.

* :func:`build_snapshot_url` — ES256-signed URL for the Apple Maps
  Snapshot service that returns a static PNG for a lat/lng pair.
  Cached in Redis for 24 hours per (venue, zoom, size, scheme) tuple
  so the backend re-signs at most once a day for any given venue
  card.

* (Task #67) :func:`fetch_nearby_poi` will hit the Maps Server API with
  a 400m radius search. Not yet implemented.

**Runtime contract.** Every public function guards on
:func:`is_configured`; if any Apple Maps credential is missing the call
raises ``APPLE_MAPS_UNAVAILABLE`` with a 503 so route handlers can
cleanly surface "maps are off on this environment" rather than minting
tokens with a placeholder key.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

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

# Snapshot Service. Apple's endpoint serves a PNG for a signed URL;
# the signature is over ``path+query`` using the same ES256 credentials
# as the MapKit JS token. One signing call is cheap but the image
# delivery itself is cached by Apple's CDN — we cache the signed URL
# string in Redis for 24 hours so identical venue-card requests skip
# the re-sign.
_SNAPSHOT_HOST = "https://snapshot.apple-mapkit.com"
_SNAPSHOT_PATH = "/api/v1/snapshot"
_SNAPSHOT_CACHE_KEY = "apple_maps:snapshot_url:v1"
_SNAPSHOT_CACHE_TTL = timedelta(hours=24)
_SNAPSHOT_MAX_WIDTH = 640
_SNAPSHOT_MAX_HEIGHT = 640
_SNAPSHOT_VALID_SCHEMES = frozenset({"light", "dark"})


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
# Snapshot Service — signed static-image URLs
# ---------------------------------------------------------------------------


def build_snapshot_url(
    *,
    latitude: float,
    longitude: float,
    zoom: float = 15.0,
    width: int = 600,
    height: int = 400,
    scale: int = 2,
    color_scheme: str = "light",
    annotation_label: str | None = None,
    redis_client: redis.Redis | None = None,
) -> str:
    """Return a 24-hour-cached, ES256-signed Apple Maps Snapshot URL.

    The returned URL can be used verbatim as an ``<img src>`` — Apple's
    CDN serves the PNG and honors the signature without any additional
    auth header. Zoom, size, and scheme are encoded into the cache key
    so differently-styled snapshots of the same venue coexist.

    Args:
        latitude: WGS-84 latitude of the map center.
        longitude: WGS-84 longitude.
        zoom: Apple zoom level (1-20). Defaults to 15 - tight enough
            for a venue but with enough context to orient a visitor.
        width: Image width in CSS pixels. Clamped to 640 (Apple cap).
        height: Image height in CSS pixels. Clamped to 640.
        scale: Retina factor — 1 or 2. Doubles the physical pixels.
        color_scheme: ``"light"`` or ``"dark"``.
        annotation_label: Optional glyph text for a red pin at center.
            Pass None for a pin-less snapshot.
        redis_client: Optional Redis client for URL caching. When None
            the module-level client is used; when unusable, the URL is
            signed fresh on every call.

    Returns:
        Fully-qualified HTTPS URL, including ``&signature=`` query.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            missing. (500) when the private key file can't be read or
            is the wrong key type.
    """
    if not is_configured():
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    width = min(max(int(width), 1), _SNAPSHOT_MAX_WIDTH)
    height = min(max(int(height), 1), _SNAPSHOT_MAX_HEIGHT)
    scale = 2 if int(scale) >= 2 else 1
    if color_scheme not in _SNAPSHOT_VALID_SCHEMES:
        color_scheme = "light"

    client = redis_client if redis_client is not None else _get_redis()
    cache_key = _build_snapshot_cache_key(
        latitude=latitude,
        longitude=longitude,
        zoom=zoom,
        width=width,
        height=height,
        scale=scale,
        color_scheme=color_scheme,
        annotation_label=annotation_label,
    )
    cached = _read_snapshot_cache(client, cache_key)
    if cached is not None:
        return cached

    settings = get_settings()
    params: list[tuple[str, str]] = [
        ("center", f"{latitude:.6f},{longitude:.6f}"),
        ("z", f"{zoom:g}"),
        ("size", f"{width}x{height}"),
        ("scale", str(scale)),
        ("colorScheme", color_scheme),
        ("teamId", settings.apple_mapkit_team_id),
        ("keyId", settings.apple_mapkit_key_id),
    ]
    if annotation_label:
        pin = [
            {
                "point": f"{latitude:.6f},{longitude:.6f}",
                "color": "red",
                "glyphText": annotation_label[:2],
            }
        ]
        params.append(("annotations", json.dumps(pin, separators=(",", ":"))))

    query = urlencode(params, quote_via=quote)
    to_sign = f"{_SNAPSHOT_PATH}?{query}"
    signature = _sign_snapshot(to_sign.encode("ascii"))
    full = f"{_SNAPSHOT_HOST}{to_sign}&signature={signature}"

    _write_snapshot_cache(client, cache_key, full)
    return full


def _sign_snapshot(message: bytes) -> str:
    """Return a base64url ES256 signature for the Snapshot API.

    Unlike :func:`_sign_token` (which wraps claims in a JWT), the
    Snapshot service expects a raw ECDSA signature over the canonical
    ``path?query`` string, concatenating ``r`` and ``s`` as 32-byte
    big-endian ints and base64url-encoding the 64-byte result.

    Args:
        message: UTF-8 bytes to sign — the path+query string.

    Returns:
        Base64url-encoded signature, unpadded, URL-safe.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (500) if the configured
            key is not an ES256 (P-256) private key.
    """
    pem = _load_private_key()
    private_key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps private key is not an ES256 (P-256) key.",
            status_code=500,
        )
    der = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_snapshot_cache_key(
    *,
    latitude: float,
    longitude: float,
    zoom: float,
    width: int,
    height: int,
    scale: int,
    color_scheme: str,
    annotation_label: str | None,
) -> str:
    """Compose a deterministic Redis key for a snapshot URL.

    The caller's inputs are hashed into a short digest so the key
    length is bounded regardless of future parameter additions.

    Args:
        latitude: Map center latitude.
        longitude: Map center longitude.
        zoom: Apple zoom level.
        width: Image width in CSS pixels.
        height: Image height in CSS pixels.
        scale: Retina scale factor.
        color_scheme: ``"light"`` or ``"dark"``.
        annotation_label: Pin glyph or None.

    Returns:
        ``apple_maps:snapshot_url:v1:<sha256-hex-prefix>``.
    """
    raw = (
        f"{latitude:.6f}|{longitude:.6f}|{zoom:g}|{width}x{height}"
        f"|s{scale}|{color_scheme}|{annotation_label or ''}"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{_SNAPSHOT_CACHE_KEY}:{digest}"


def _read_snapshot_cache(client: redis.Redis | None, cache_key: str) -> str | None:
    """Return a cached snapshot URL string, or None.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.

    Returns:
        The URL string when the cache hit is a non-empty, well-formed
        Apple Maps URL. Otherwise None.
    """
    if client is None:
        return None
    try:
        raw = client.get(cache_key)
    except Exception:
        logger.warning("apple_maps_snapshot_cache_read_failed")
        return None
    if not raw:
        return None
    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError:
        return None
    if not decoded.startswith(_SNAPSHOT_HOST):
        return None
    return decoded


def _write_snapshot_cache(client: redis.Redis | None, cache_key: str, url: str) -> None:
    """Persist a snapshot URL string in Redis with a 24-hour TTL.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.
        url: The signed snapshot URL to cache.
    """
    if client is None:
        return
    try:
        client.set(
            cache_key,
            url,
            ex=int(_SNAPSHOT_CACHE_TTL.total_seconds()),
        )
    except Exception:
        logger.warning("apple_maps_snapshot_cache_write_failed")


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
        client.set(
            cache_key,
            json.dumps(payload),
            ex=int(_MAPKIT_CACHE_TTL.total_seconds()),
        )
    except Exception:
        logger.warning("apple_maps_cache_write_failed")


__all__ = [
    "build_snapshot_url",
    "is_configured",
    "mint_mapkit_token",
    "reset_redis_client_for_tests",
]
