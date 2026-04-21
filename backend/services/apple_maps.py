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

* :func:`fetch_nearby_poi` — calls Apple's Maps Server API
  ``/v1/searchNearby`` for a lat/lng and returns restaurants, bars,
  and cafes within a 400 m radius. Results are cached in Redis for
  7 days; the backend's Apple access token is cached for its natural
  lifetime (typically 30 minutes).

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
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlencode

import jwt
import requests
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

# Maps Server API. Unlike the Snapshot and MapKit JS flows, every
# request to ``maps-api.apple.com`` must first exchange a developer
# JWT for a short-lived access token. We cache that access token in
# Redis for its natural lifetime so the overwhelming majority of
# ``/searchNearby`` calls skip the exchange entirely.
_MAPS_API_HOST = "https://maps-api.apple.com"
_NEARBY_CACHE_KEY = "apple_maps:nearby_poi:v1"
_NEARBY_CACHE_TTL = timedelta(days=7)
_NEARBY_RADIUS_M_DEFAULT = 400
_NEARBY_LIMIT_DEFAULT = 12
_ACCESS_TOKEN_CACHE_KEY = "apple_maps:access_token:v1"
_ACCESS_TOKEN_SAFETY_MARGIN_S = 60
_DEFAULT_POI_CATEGORIES: tuple[str, ...] = ("Restaurant", "Bar", "Cafe")
_NEARBY_HTTP_TIMEOUT = 5.0


class _HttpClient(Protocol):
    """Minimal protocol the Maps Server API consumes from requests-like clients."""

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = ...,
        headers: dict[str, str] | None = ...,
        timeout: float = ...,
    ) -> Any:
        """Issue an HTTP GET and return a response-like object."""


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
# Maps Server API — nearby POI search
# ---------------------------------------------------------------------------


def fetch_nearby_poi(
    *,
    latitude: float,
    longitude: float,
    categories: tuple[str, ...] = _DEFAULT_POI_CATEGORIES,
    radius_m: int = _NEARBY_RADIUS_M_DEFAULT,
    limit: int = _NEARBY_LIMIT_DEFAULT,
    http_client: _HttpClient | None = None,
    redis_client: redis.Redis | None = None,
) -> list[dict[str, Any]]:
    """Return a list of POIs near a venue via Apple's Maps Server API.

    Calls ``GET /v1/searchNearby`` with the venue's coordinates and the
    supplied category filter, then post-filters by a hard ``radius_m``
    cap (Apple's own region may be larger) and sorts by ascending
    distance. The result list is cached in Redis for 7 days keyed by
    (lat, lng, categories, radius) — a venue's neighborhood does not
    churn often.

    Args:
        latitude: Venue latitude in WGS-84.
        longitude: Venue longitude.
        categories: Apple POI category names to include. Defaults to
            restaurant / bar / cafe, which is what the venue detail
            page needs today.
        radius_m: Hard cap on distance from the venue in meters. POIs
            beyond this cap are dropped.
        limit: Maximum number of POIs to return after filtering.
        http_client: Optional requests-compatible client (for tests).
            When None the module-level ``requests`` library is used.
        redis_client: Optional Redis client. When None the module-level
            client is used.

    Returns:
        A list of dicts of shape
        ``{name, category, address, latitude, longitude, distance_m}``.
        Empty list when Apple returns nothing useful.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) if credentials are
            missing; (502) if Apple's API returns a non-2xx response.
    """
    if not is_configured():
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    client = redis_client if redis_client is not None else _get_redis()
    cache_key = _build_nearby_cache_key(
        latitude=latitude,
        longitude=longitude,
        categories=categories,
        radius_m=radius_m,
    )
    cached = _read_nearby_cache(client, cache_key)
    if cached is not None:
        return cached

    http = http_client if http_client is not None else requests
    access_token = _get_access_token(client=client, http_client=http)

    params: dict[str, str] = {
        "loc": f"{latitude:.6f},{longitude:.6f}",
        "includePoiCategories": ",".join(categories),
        "lang": "en-US",
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = http.get(
            f"{_MAPS_API_HOST}/v1/searchNearby",
            params=params,
            headers=headers,
            timeout=_NEARBY_HTTP_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("apple_maps_nearby_http_failed")
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps nearby lookup failed.",
            status_code=502,
        ) from exc

    if not _is_ok(response):
        status = getattr(response, "status_code", "?")
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message=f"Apple Maps /searchNearby returned {status}.",
            status_code=502,
        )

    try:
        body = response.json()
    except Exception as exc:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps /searchNearby returned a non-JSON body.",
            status_code=502,
        ) from exc

    results = _normalize_nearby_results(
        raw=body.get("results") or [],
        origin_lat=latitude,
        origin_lng=longitude,
        radius_m=radius_m,
        limit=limit,
    )
    _write_nearby_cache(client, cache_key, results)
    return results


def _normalize_nearby_results(
    *,
    raw: list[Any],
    origin_lat: float,
    origin_lng: float,
    radius_m: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Convert Apple's POI records into the shape the API layer returns.

    Args:
        raw: Records pulled from Apple's ``results`` array.
        origin_lat: Venue latitude, used for distance filtering.
        origin_lng: Venue longitude.
        radius_m: Hard distance cap in meters.
        limit: Max items to retain after sorting by distance.

    Returns:
        Sorted, trimmed list of normalized POI dicts.
    """
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        coord = item.get("coordinate") or {}
        plat = coord.get("latitude")
        plng = coord.get("longitude")
        if not isinstance(plat, (int, float)) or not isinstance(plng, (int, float)):
            continue
        distance = _haversine_m(origin_lat, origin_lng, plat, plng)
        if distance > radius_m:
            continue
        address_lines = item.get("formattedAddressLines") or []
        address = ", ".join(str(line) for line in address_lines if line) or None
        normalized.append(
            {
                "name": item.get("name"),
                "category": item.get("poiCategory"),
                "address": address,
                "latitude": float(plat),
                "longitude": float(plng),
                "distance_m": round(distance),
            }
        )
    normalized.sort(key=lambda poi: poi["distance_m"])
    return normalized[:limit]


def _is_ok(response: Any) -> bool:
    """Return True if the HTTP response represents a 2xx status.

    Args:
        response: A requests-like response with either ``status_code``
            or an ``ok`` attribute.

    Returns:
        True when the response is 2xx.
    """
    ok = getattr(response, "ok", None)
    if isinstance(ok, bool):
        return ok
    status = getattr(response, "status_code", 500)
    return isinstance(status, int) and 200 <= status < 300


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points in meters.

    Args:
        lat1: First point latitude in degrees.
        lng1: First point longitude in degrees.
        lat2: Second point latitude in degrees.
        lng2: Second point longitude in degrees.

    Returns:
        Distance along the Earth's surface, in meters, using the WGS-84
        mean radius (6371008.8 m).
    """
    earth_radius_m = 6_371_008.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_m * 2 * math.asin(math.sqrt(a))


def _get_access_token(
    *,
    client: redis.Redis | None,
    http_client: _HttpClient,
) -> str:
    """Return a cached or freshly-exchanged Apple Maps access token.

    Apple requires every Maps Server API call to present a short-lived
    access token minted from a developer JWT. We cache the access token
    in Redis for its natural lifetime minus a 60-second safety margin;
    the JWT itself is cheap to sign and not reused.

    Args:
        client: Redis client or None. When None the token is always
            minted fresh and not cached.
        http_client: A requests-compatible client for the exchange call.

    Returns:
        The bearer access token string.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (502) when Apple rejects
            the exchange or returns an unparseable body.
    """
    if client is not None:
        try:
            raw = client.get(_ACCESS_TOKEN_CACHE_KEY)
        except Exception:
            raw = None
        if raw:
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    developer_jwt = _sign_server_auth_jwt()
    try:
        response = http_client.get(
            f"{_MAPS_API_HOST}/v1/token",
            headers={"Authorization": f"Bearer {developer_jwt}"},
            timeout=_NEARBY_HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps token exchange failed.",
            status_code=502,
        ) from exc

    if not _is_ok(response):
        status = getattr(response, "status_code", "?")
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message=f"Apple Maps /token returned {status}.",
            status_code=502,
        )
    try:
        body = response.json()
    except Exception as exc:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps /token returned a non-JSON body.",
            status_code=502,
        ) from exc

    token = body.get("accessToken")
    if not isinstance(token, str) or not token:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps /token response missing accessToken.",
            status_code=502,
        )
    expires_in = body.get("expiresInSeconds")
    ttl = int(expires_in) if isinstance(expires_in, int) else 1800
    if client is not None:
        try:
            cache_ttl = max(
                _ACCESS_TOKEN_SAFETY_MARGIN_S,
                ttl - _ACCESS_TOKEN_SAFETY_MARGIN_S,
            )
            client.set(_ACCESS_TOKEN_CACHE_KEY, token, ex=cache_ttl)
        except Exception:
            logger.warning("apple_maps_access_token_cache_write_failed")
    return token


def _sign_server_auth_jwt() -> str:
    """Sign the short-lived developer JWT that the ``/v1/token`` endpoint expects.

    The JWT shape matches MapKit JS's except there is no ``origin``
    claim: the token is used server-to-server only.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, str | int] = {
        "iss": settings.apple_mapkit_team_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    headers = {
        "alg": _ALGORITHM,
        "kid": settings.apple_mapkit_key_id,
        "typ": "JWT",
    }
    pem = _load_private_key()
    return jwt.encode(claims, pem, algorithm=_ALGORITHM, headers=headers)


def _build_nearby_cache_key(
    *,
    latitude: float,
    longitude: float,
    categories: tuple[str, ...],
    radius_m: int,
) -> str:
    """Return a deterministic Redis key for a nearby-POI result set.

    Args:
        latitude: Center latitude.
        longitude: Center longitude.
        categories: Tuple of Apple POI category filters applied.
        radius_m: Distance cap in meters.

    Returns:
        ``apple_maps:nearby_poi:v1:<sha256-hex-prefix>``.
    """
    raw = f"{latitude:.5f}|{longitude:.5f}|{radius_m}|{'/'.join(sorted(categories))}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{_NEARBY_CACHE_KEY}:{digest}"


def _read_nearby_cache(
    client: redis.Redis | None, cache_key: str
) -> list[dict[str, Any]] | None:
    """Return a cached nearby-POI list, or None.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.

    Returns:
        The parsed list when the cache hit is a valid JSON array of
        dicts. Otherwise None.
    """
    if client is None:
        return None
    try:
        raw = client.get(cache_key)
    except Exception:
        logger.warning("apple_maps_nearby_cache_read_failed")
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    # Defensive: only return items that look like our normalized shape.
    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            cleaned.append(item)
    return cleaned


def _write_nearby_cache(
    client: redis.Redis | None,
    cache_key: str,
    results: list[dict[str, Any]],
) -> None:
    """Persist a nearby-POI list in Redis with a 7-day TTL.

    Args:
        client: Redis client or None.
        cache_key: Fully-qualified Redis key.
        results: The normalized POI list to cache.
    """
    if client is None:
        return
    try:
        client.set(
            cache_key,
            json.dumps(results),
            ex=int(_NEARBY_CACHE_TTL.total_seconds()),
        )
    except Exception:
        logger.warning("apple_maps_nearby_cache_write_failed")


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


# ---------------------------------------------------------------------------
# Typed public surface — used by the map flows and recommendation verifier
# ---------------------------------------------------------------------------


# Apple's geocode endpoint shares the access-token lifecycle with searchNearby,
# but it is not a per-venue lookup — it's "did this user-typed string resolve
# to a real Apple Maps place?" Verification is the gate for community-submitted
# recommendations: a name-only or address-only submission must round-trip
# through Apple before it can land in the database. The 0.80 cutoff is the
# sweet spot from prior experience — high enough to keep "Black Cat" and
# "Black Cat DC" together, low enough to reject "Black Cat" → "Howard Theatre".
_GEOCODE_PATH = "/v1/geocode"
_PLACE_VERIFY_SIMILARITY_FLOOR = 0.80


@dataclass(frozen=True, slots=True)
class NearbyPlace:
    """A normalized POI returned from Apple Maps' searchNearby surface.

    Attributes:
        name: Place display name (e.g. ``"The Gibson"``).
        category: Apple POI category string (e.g. ``"Bar"``); may be
            None when the upstream record had no category.
        address: Comma-joined formatted address lines, or None.
        latitude: WGS-84 latitude in degrees.
        longitude: WGS-84 longitude in degrees.
        distance_m: Great-circle distance from the search anchor in
            meters, rounded to the nearest integer.
    """

    name: str
    category: str | None
    address: str | None
    latitude: float
    longitude: float
    distance_m: int


@dataclass(frozen=True, slots=True)
class VerifiedPlace:
    """An Apple Maps place that passed the verification similarity gate.

    Attributes:
        name: Canonical place name as Apple returned it.
        address: Canonical formatted address, or None if Apple omitted it.
        latitude: WGS-84 latitude.
        longitude: WGS-84 longitude.
        similarity: SequenceMatcher ratio against the caller's query,
            in ``[0.0, 1.0]``. Always ``>= 0.80`` for a returned value.
    """

    name: str
    address: str | None
    latitude: float
    longitude: float
    similarity: float


def search_nearby_places(
    *,
    latitude: float,
    longitude: float,
    categories: tuple[str, ...] = _DEFAULT_POI_CATEGORIES,
    radius_m: int = _NEARBY_RADIUS_M_DEFAULT,
    limit: int = _NEARBY_LIMIT_DEFAULT,
    http_client: _HttpClient | None = None,
    redis_client: redis.Redis | None = None,
) -> list[NearbyPlace]:
    """Typed wrapper around :func:`fetch_nearby_poi` for map-side callers.

    Used by the map flows and the community recommendation form to
    autocomplete a "what place are you recommending" picker. Returns
    a list of :class:`NearbyPlace` instead of raw dicts so callers
    don't have to re-validate the shape.

    Args:
        latitude: Search anchor latitude.
        longitude: Search anchor longitude.
        categories: Apple POI category filter, default restaurant /
            bar / cafe.
        radius_m: Hard distance cap in meters.
        limit: Max results after sorting by ascending distance.
        http_client: Optional requests-compatible client (tests only).
        redis_client: Optional Redis client; same semantics as
            :func:`fetch_nearby_poi`.

    Returns:
        List of :class:`NearbyPlace`, ascending by distance.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            missing or (502) when Apple's API errors out.
    """
    raw = fetch_nearby_poi(
        latitude=latitude,
        longitude=longitude,
        categories=categories,
        radius_m=radius_m,
        limit=limit,
        http_client=http_client,
        redis_client=redis_client,
    )
    return [
        NearbyPlace(
            name=str(item.get("name") or ""),
            category=item.get("category"),
            address=item.get("address"),
            latitude=float(item["latitude"]),
            longitude=float(item["longitude"]),
            distance_m=int(item["distance_m"]),
        )
        for item in raw
        if item.get("name")
    ]


def verify_place_by_name(
    *,
    query: str,
    near_latitude: float,
    near_longitude: float,
    http_client: _HttpClient | None = None,
    redis_client: redis.Redis | None = None,
) -> VerifiedPlace | None:
    """Resolve a user-typed place name through Apple's geocoder.

    Used as the first verification step for community recommendations:
    a recommendation is only allowed to land in the database after the
    submitter's free-text "place name" passes a geocode lookup with
    sufficient similarity to the canonical Apple result. Returns None
    when Apple has no result or when the similarity gate rejects the
    only candidate.

    Args:
        query: User-supplied place name.
        near_latitude: Anchor latitude that biases the search toward
            the city the recommendation is being submitted in.
        near_longitude: Anchor longitude.
        http_client: Optional requests-compatible client.
        redis_client: Optional Redis client (used only for the access-
            token cache; geocode results are not cached because the
            input is unbounded user text).

    Returns:
        A :class:`VerifiedPlace` with the canonical Apple data when
        the gate passes; ``None`` otherwise.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            missing or (502) when Apple's API errors out.
    """
    return _geocode_with_similarity(
        query=query,
        compare_to=lambda result: str(result.get("name") or ""),
        params={
            "q": query,
            "searchLocation": f"{near_latitude:.6f},{near_longitude:.6f}",
            "lang": "en-US",
        },
        http_client=http_client,
        redis_client=redis_client,
    )


def verify_place_by_address(
    *,
    query: str,
    http_client: _HttpClient | None = None,
    redis_client: redis.Redis | None = None,
) -> VerifiedPlace | None:
    """Resolve a user-typed street address through Apple's geocoder.

    Same gate as :func:`verify_place_by_name`, but compares the user's
    input against the canonical formatted address Apple returns instead
    of the place name. Used when the recommendation surface lets the
    user type "1811 14th St NW" instead of a place name.

    Args:
        query: User-supplied address string.
        http_client: Optional requests-compatible client.
        redis_client: Optional Redis client.

    Returns:
        A :class:`VerifiedPlace` when the address matches Apple's
        canonical result above the similarity floor; ``None`` otherwise.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            missing or (502) when Apple's API errors out.
    """
    return _geocode_with_similarity(
        query=query,
        compare_to=lambda result: _formatted_address(result) or "",
        params={"q": query, "lang": "en-US"},
        http_client=http_client,
        redis_client=redis_client,
    )


def _geocode_with_similarity(
    *,
    query: str,
    compare_to: Any,
    params: dict[str, str],
    http_client: _HttpClient | None,
    redis_client: redis.Redis | None,
) -> VerifiedPlace | None:
    """Call Apple's ``/v1/geocode`` and apply the similarity gate.

    Args:
        query: User-supplied query, used for the similarity comparison.
        compare_to: Callable that extracts the comparison string out of
            an Apple result dict (the place name or formatted address).
        params: Query parameters to send to ``/v1/geocode``.
        http_client: Optional requests-compatible client.
        redis_client: Optional Redis client.

    Returns:
        A :class:`VerifiedPlace` when the top result clears the floor;
        ``None`` when Apple returned nothing or the score is too low.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            missing or (502) when Apple's API errors out.
    """
    if not is_configured():
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )
    cleaned = (query or "").strip()
    if not cleaned:
        return None

    client = redis_client if redis_client is not None else _get_redis()
    http = http_client if http_client is not None else requests
    access_token = _get_access_token(client=client, http_client=http)

    try:
        response = http.get(
            f"{_MAPS_API_HOST}{_GEOCODE_PATH}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_NEARBY_HTTP_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("apple_maps_geocode_http_failed")
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps geocode request failed.",
            status_code=502,
        ) from exc

    if not _is_ok(response):
        status = getattr(response, "status_code", "?")
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message=f"Apple Maps /geocode returned {status}.",
            status_code=502,
        )

    try:
        body = response.json()
    except Exception as exc:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps /geocode returned a non-JSON body.",
            status_code=502,
        ) from exc

    results = body.get("results") or []
    if not isinstance(results, list) or not results:
        return None
    top = results[0]
    if not isinstance(top, dict):
        return None
    coord = top.get("coordinate") or {}
    plat = coord.get("latitude")
    plng = coord.get("longitude")
    if not isinstance(plat, (int, float)) or not isinstance(plng, (int, float)):
        return None
    canonical = compare_to(top)
    similarity = _normalized_similarity(cleaned, canonical)
    if similarity < _PLACE_VERIFY_SIMILARITY_FLOOR:
        return None
    return VerifiedPlace(
        name=str(top.get("name") or canonical),
        address=_formatted_address(top),
        latitude=float(plat),
        longitude=float(plng),
        similarity=similarity,
    )


def _formatted_address(result: dict[str, Any]) -> str | None:
    """Join Apple's ``formattedAddressLines`` into one comma-separated string.

    Args:
        result: A single Apple Maps result dict.

    Returns:
        The joined address string, or None when Apple omitted address
        lines entirely.
    """
    lines = result.get("formattedAddressLines") or []
    if not isinstance(lines, list):
        return None
    joined = ", ".join(str(line) for line in lines if line)
    return joined or None


def _normalized_similarity(left: str, right: str) -> float:
    """Return a case-insensitive SequenceMatcher ratio between two strings.

    Punctuation differences (commas, hyphens, periods) and casing
    swamp the underlying ratio if compared verbatim. We lowercase and
    strip non-alphanumeric characters first so "Black Cat, DC" and
    "Black Cat DC" resolve as equivalent.

    Args:
        left: The user's query.
        right: The canonical Apple value to compare against.

    Returns:
        A ratio in ``[0.0, 1.0]``. Empty strings on either side return 0.
    """

    def _canonicalize(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
        return " ".join(cleaned.split())

    a = _canonicalize(left)
    b = _canonicalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(a=a, b=b).ratio()


__all__ = [
    "NearbyPlace",
    "VerifiedPlace",
    "build_snapshot_url",
    "fetch_nearby_poi",
    "is_configured",
    "mint_mapkit_token",
    "reset_redis_client_for_tests",
    "search_nearby_places",
    "verify_place_by_address",
    "verify_place_by_name",
]
