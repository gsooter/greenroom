"""HTTP and JWT-verification client for the Knuckles identity service.

Knuckles is the centralized identity service that issues every Greenroom
access token (Decision 0XX — see DECISIONS.md). This module is the only
place Greenroom talks to it. It does two unrelated jobs that happen to
share a base URL and an app-client credential:

1. **JWT verification.** Verifies RS256 access tokens against the JWKS
   document published at ``GET {KNUCKLES_URL}/.well-known/jwks.json``.
   Keys are cached in memory with a TTL; a token signed with an unknown
   ``kid`` triggers an immediate JWKS refresh to handle key rotation.
2. **App-client HTTP proxy.** Posts to Knuckles ``/v1/auth/*`` endpoints
   (magic-link start, token exchange, passkey ceremonies) carrying the
   ``X-Client-Id`` / ``X-Client-Secret`` headers Greenroom was issued.

This file is purely additive — the legacy HS256 ``backend.core.auth``
module continues to issue and verify tokens until ``require_auth`` is
rewired to call :func:`verify_knuckles_token`.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from backend.core.config import get_settings
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
)
from backend.core.logging import get_logger

logger = get_logger(__name__)

_ALGORITHM = "RS256"
_JWKS_PATH = "/.well-known/jwks.json"
_DISK_CACHE_FILENAME = "greenroom_knuckles_jwks.json"


@dataclass
class _JwksCache:
    """In-memory snapshot of the Knuckles JWKS keyed by ``kid``."""

    keys: dict[str, RSAPublicKey]
    fetched_at: float


_cache: _JwksCache | None = None
_cache_lock = threading.Lock()


def _jwks_url() -> str:
    """Return the absolute URL of the Knuckles JWKS document.

    Returns:
        The JWKS URL with no trailing slash mid-path.
    """
    settings = get_settings()
    return settings.knuckles_url.rstrip("/") + _JWKS_PATH


def _ttl_expired(fetched_at: float) -> bool:
    """Return True when the cache snapshot is older than the configured TTL.

    Args:
        fetched_at: Unix timestamp the cached JWKS was fetched.

    Returns:
        True if a refresh is due based on
        ``settings.knuckles_jwks_cache_ttl_seconds``.
    """
    settings = get_settings()
    return time.time() - fetched_at > settings.knuckles_jwks_cache_ttl_seconds


def _parse_jwks_entries(
    entries: list[dict[str, Any]],
) -> dict[str, RSAPublicKey]:
    """Parse a list of JWK entries into a ``kid`` → public-key map.

    Non-RSA keys and entries missing a string ``kid`` are silently
    skipped; Knuckles only publishes RSA keys today, but the filter
    keeps the parser forward-compatible.

    Args:
        entries: The ``keys`` array from a JWKS document.

    Returns:
        Mapping from ``kid`` to RSA public key.
    """
    keys: dict[str, RSAPublicKey] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str):
            continue
        try:
            public_key = RSAAlgorithm.from_jwk(entry)
        except Exception:
            continue
        if isinstance(public_key, RSAPublicKey):
            keys[kid] = public_key
    return keys


def _fetch_jwks() -> tuple[dict[str, RSAPublicKey], list[dict[str, Any]]]:
    """Fetch the JWKS from Knuckles and return parsed keys + raw entries.

    The raw entries are returned alongside the parsed keys so the
    caller can persist them to the disk cache verbatim, avoiding a
    reserialization round-trip.

    Returns:
        Tuple of ``(kid → public-key map, raw JWK entries)``.

    Raises:
        AppError: ``INVALID_TOKEN`` (401) if the JWKS endpoint is
            unreachable or returns a non-2xx response. The error code
            is reused because, from the caller's perspective, an
            unverifiable token is indistinguishable from an invalid one.
    """
    try:
        response = requests.get(_jwks_url(), timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Could not fetch identity service JWKS.",
            status_code=401,
        ) from exc

    document = response.json()
    raw_entries = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(raw_entries, list):
        raw_entries = []
    return _parse_jwks_entries(raw_entries), raw_entries


def _disk_cache_path() -> Path:
    """Return the filesystem path of the shared JWKS disk cache.

    The cache sits in the system temp directory so sibling Gunicorn
    workers in the same container can share it — when one worker
    fetches the JWKS after a cold start, the others read the result
    instead of dog-piling the Knuckles endpoint.

    Returns:
        Path to the JSON file holding the cached JWKS snapshot.
    """
    return Path(tempfile.gettempdir()) / _DISK_CACHE_FILENAME


def _load_jwks_from_disk() -> _JwksCache | None:
    """Load the most recent JWKS snapshot from disk, if any.

    Returns:
        A ``_JwksCache`` reconstructed from the persisted document, or
        ``None`` if the file is missing, unreadable, malformed, or
        contains zero usable keys.
    """
    path = _disk_cache_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    fetched_at = payload.get("fetched_at")
    raw_entries = payload.get("keys")
    if not isinstance(fetched_at, int | float) or not isinstance(raw_entries, list):
        return None

    keys = _parse_jwks_entries(raw_entries)
    if not keys:
        return None
    return _JwksCache(keys=keys, fetched_at=float(fetched_at))


def _persist_jwks_to_disk(raw_entries: list[dict[str, Any]], fetched_at: float) -> None:
    """Atomically write the JWKS snapshot to the shared disk cache.

    Writes to a sibling ``.tmp`` file and ``os.replace``s it into
    place so a crashed or concurrent write never leaves a torn file
    on disk. Disk errors are logged and swallowed — the disk cache
    is an optimization, not a correctness requirement.

    Args:
        raw_entries: The ``keys`` array from the fetched JWKS.
        fetched_at: Unix timestamp to stamp the snapshot with.
    """
    path = _disk_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".jwks-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": fetched_at, "keys": raw_entries}, handle)
        os.replace(tmp_name, path)
    except OSError:
        logger.warning("jwks_disk_cache_write_failed", extra={"path": str(path)})


def reset_jwks_cache() -> None:
    """Drop the cached JWKS snapshot.

    Tests call this between cases that monkeypatch the JWKS endpoint
    or rotate the signing key. Production code never needs to call it
    because the TTL + kid-miss-refresh path handles rotation.
    """
    global _cache
    with _cache_lock:
        _cache = None


def _refresh_jwks_cache() -> _JwksCache:
    """Fetch the JWKS from Knuckles, update the disk cache, and return it.

    Returns:
        The freshly-built in-memory cache snapshot.

    Raises:
        AppError: Propagates ``_fetch_jwks``'s ``INVALID_TOKEN`` on
            network or protocol errors.
    """
    keys, raw_entries = _fetch_jwks()
    fetched_at = time.time()
    _persist_jwks_to_disk(raw_entries, fetched_at)
    return _JwksCache(keys=keys, fetched_at=fetched_at)


def _get_signing_key(kid: str) -> RSAPublicKey:
    """Return the public key for the given ``kid``, refreshing the JWKS if needed.

    Refresh policy:

    * Cold start (no in-memory cache) tries the shared disk cache
      first. A fresh-enough snapshot skips the network hop entirely,
      which matters when a new Gunicorn worker boots while a sibling
      has already fetched the JWKS.
    * TTL expiry forces a network refresh.
    * A cache miss for an otherwise-fresh snapshot also triggers a
      refresh — that's how a consuming app picks up a freshly rotated
      Knuckles key without waiting for the TTL.

    Args:
        kid: The ``kid`` header from the access token under verification.

    Returns:
        The RSA public key registered under that ``kid``.

    Raises:
        AppError: ``INVALID_TOKEN`` (401) if no key with that ``kid``
            is published even after a forced refresh.
    """
    global _cache
    with _cache_lock:
        if _cache is None:
            disk = _load_jwks_from_disk()
            if disk is not None and not _ttl_expired(disk.fetched_at):
                _cache = disk

        if _cache is None or _ttl_expired(_cache.fetched_at):
            _cache = _refresh_jwks_cache()

        if kid not in _cache.keys:
            _cache = _refresh_jwks_cache()

        if kid not in _cache.keys:
            raise AppError(
                code=INVALID_TOKEN,
                message="Access token signed with an unknown key.",
                status_code=401,
            )
        return _cache.keys[kid]


def verify_knuckles_token(token: str) -> dict[str, Any]:
    """Verify a Knuckles-issued RS256 access token and return its claims.

    Validates signature, issuer (``KNUCKLES_URL``), audience
    (``KNUCKLES_CLIENT_ID``), and the standard expiry claims. The
    returned dict is the full claim set as Knuckles emitted it; callers
    typically read ``sub`` (Knuckles ``users.id``) and ``email``.

    Args:
        token: The raw bearer-token value from an ``Authorization`` header.

    Returns:
        The decoded claims dictionary.

    Raises:
        AppError: ``TOKEN_EXPIRED`` (401) if the token's ``exp`` is in
            the past. ``INVALID_TOKEN`` (401) for any other failure
            (malformed header, missing/unknown ``kid``, bad signature,
            audience or issuer mismatch).
    """
    settings = get_settings()
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token header is malformed.",
            status_code=401,
        ) from exc

    kid = unverified_header.get("kid")
    if not isinstance(kid, str):
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token is missing a key id.",
            status_code=401,
        )

    public_key = _get_signing_key(kid)
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=[_ALGORITHM],
            audience=settings.knuckles_client_id,
            issuer=settings.knuckles_url,
            options={"require": ["iss", "sub", "aud", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            code=TOKEN_EXPIRED,
            message="Access token has expired.",
            status_code=401,
        ) from exc
    except jwt.PyJWTError as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token is invalid.",
            status_code=401,
        ) from exc
    return claims


# ---------------------------------------------------------------------------
# App-client HTTP proxy
# ---------------------------------------------------------------------------


class KnucklesHTTPError(AppError):
    """Knuckles returned a non-2xx response on an app-client call."""

    def __init__(self, *, message: str, status_code: int) -> None:
        """Initialize a KnucklesHTTPError.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status returned by Knuckles, propagated
                so the Greenroom route can surface the same code.
        """
        super().__init__(
            code="KNUCKLES_HTTP_ERROR",
            message=message,
            status_code=status_code,
        )


def _client_headers() -> dict[str, str]:
    """Return the ``X-Client-Id`` / ``X-Client-Secret`` header pair.

    Returns:
        Dict of headers to attach to every outbound Knuckles call.
    """
    settings = get_settings()
    return {
        "X-Client-Id": settings.knuckles_client_id,
        "X-Client-Secret": settings.knuckles_client_secret,
    }


def post(
    path: str,
    *,
    json: dict[str, Any] | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """POST to a Knuckles app-client endpoint.

    Args:
        path: Path under the Knuckles base URL (e.g.
            ``/v1/auth/magic-link/start``). Must start with ``/``.
        json: Optional JSON body. ``None`` sends ``{}``.
        bearer_token: Optional user access token to forward as
            ``Authorization: Bearer``. Required by Knuckles endpoints
            that sit behind ``@require_auth`` (e.g. passkey register).
            App-client headers are always sent regardless.

    Returns:
        The decoded JSON response from Knuckles.

    Raises:
        KnucklesHTTPError: If Knuckles returns a non-2xx status. The
            exception's ``status_code`` mirrors what Knuckles returned.
    """
    settings = get_settings()
    url = settings.knuckles_url.rstrip("/") + path
    headers = _client_headers()
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.post(
        url,
        json=json or {},
        headers=headers,
        timeout=10,
    )
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text}}
        error = payload.get("error") if isinstance(payload, dict) else None
        message = (
            error.get("message", "Knuckles request failed.")
            if isinstance(error, dict)
            else "Knuckles request failed."
        )
        raise KnucklesHTTPError(message=message, status_code=response.status_code)

    decoded: dict[str, Any] = response.json()
    return decoded


__all__ = [
    "INVALID_TOKEN",
    "TOKEN_EXPIRED",
    "KnucklesHTTPError",
    "post",
    "reset_jwks_cache",
    "verify_knuckles_token",
]
