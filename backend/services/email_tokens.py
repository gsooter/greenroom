"""HMAC-signed unsubscribe tokens for outbound email links.

Every email Greenroom sends carries a token in the unsubscribe URL of
its footer (and in the RFC 8058 ``List-Unsubscribe`` header). When a
recipient clicks the link or a mailbox provider auto-clicks for them,
the unsubscribe endpoint passes the token through :func:`verify_unsubscribe_token`
to prove the request really came from us — no auth header, no
session — and to scope the unsubscribe to the right user.

Tokens are deliberately self-contained: the user id and the scope
("all", "weekly_digest", "staff_picks", …) live in the payload, and
the signature ties them to a TTL. This keeps the unsubscribe endpoint
stateless and lets us roll the signing key without invalidating the
universe of existing pause-link footers (we just stop minting new
ones with the old key).

Format::

    <header_b64>.<payload_b64>.<sig_b64>

All segments are URL-safe base64 with the trailing ``=`` padding
stripped. ``header`` carries the algorithm + version, ``payload``
carries ``user_id``/``scope``/``iat``, and ``sig`` is HMAC-SHA256
over ``header.payload`` with the configured secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from backend.core.config import get_settings
from backend.core.exceptions import ValidationError

# Each scope corresponds either to a ``NotificationPreferences`` boolean
# column (so we can flip exactly one channel off) or to the special
# ``"all"`` scope, which routes through the global pause-all path.
_VALID_SCOPES: frozenset[str] = frozenset(
    {
        "all",
        "artist_announcements",
        "venue_announcements",
        "selling_fast_alerts",
        "show_reminders",
        "staff_picks",
        "artist_spotlights",
        "similar_artist_suggestions",
        "weekly_digest",
    }
)

# 90 days. Longer than any reasonable digest cadence so a stale Inbox
# search still gives the user a working unsubscribe link, short
# enough to bound the blast radius of a leaked token.
_TOKEN_TTL_SECONDS: int = 90 * 86400

_HEADER: dict[str, str | int] = {"alg": "HS256", "v": 1}


@dataclass(frozen=True)
class UnsubscribeToken:
    """Decoded unsubscribe-token payload.

    Attributes:
        user_id: UUID of the user the token was minted for.
        scope: Email channel the token unsubscribes from. ``"all"``
            means the recipient pressed "pause everything"; any other
            value names a specific ``NotificationPreferences`` boolean
            column.
        issued_at: Unix epoch (seconds) when the token was minted.
            Used by callers that want to log token age.
    """

    user_id: uuid.UUID
    scope: str
    issued_at: int


def mint_unsubscribe_token(user_id: uuid.UUID, scope: str) -> str:
    """Create a signed unsubscribe token for the given user and scope.

    Args:
        user_id: UUID of the user the token authorises.
        scope: ``"all"`` or one of the per-type scopes in
            :data:`_VALID_SCOPES`.

    Returns:
        A URL-safe ``header.payload.signature`` triple.

    Raises:
        ValueError: If ``scope`` is not in :data:`_VALID_SCOPES`. This
            is a developer error — scopes are a fixed enum, so an
            unknown scope means the caller is wrong, not that user
            input is malformed.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(f"Unknown unsubscribe scope: {scope!r}")
    payload = {
        "sub": str(user_id),
        "scope": scope,
        "iat": int(_now()),
    }
    head_b64 = _b64encode(json.dumps(_HEADER, separators=(",", ":")).encode())
    body_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{head_b64}.{body_b64}"
    sig = _sign(signing_input, _signing_key())
    return f"{signing_input}.{sig}"


def verify_unsubscribe_token(
    token: str, *, expected_scope: str | None = None
) -> UnsubscribeToken:
    """Verify a token's signature, expiry, and (optionally) scope.

    Args:
        token: The ``header.payload.signature`` string to verify.
        expected_scope: When provided, the token's scope must match
            exactly. ``None`` accepts any valid scope — useful for the
            shared "manage subscriptions" landing.

    Returns:
        The parsed :class:`UnsubscribeToken`.

    Raises:
        ValidationError: If the token is malformed, the signature is
            wrong, the token has expired, or the scope does not match
            ``expected_scope``.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        raise ValidationError("Unsubscribe token is malformed.")

    head_b64, body_b64, sig = token.split(".")
    if not head_b64 or not body_b64 or not sig:
        raise ValidationError("Unsubscribe token is malformed.")

    expected_sig = _sign(f"{head_b64}.{body_b64}", _signing_key())
    if not hmac.compare_digest(sig, expected_sig):
        raise ValidationError("Unsubscribe token signature did not verify.")

    try:
        payload = json.loads(_b64decode(body_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("Unsubscribe token payload is malformed.") from exc

    if not isinstance(payload, dict):
        raise ValidationError("Unsubscribe token payload is malformed.")

    sub_raw = payload.get("sub")
    scope = payload.get("scope")
    iat = payload.get("iat")
    if (
        not isinstance(sub_raw, str)
        or not isinstance(scope, str)
        or not isinstance(iat, int)
    ):
        raise ValidationError("Unsubscribe token payload is malformed.")

    try:
        user_id = uuid.UUID(sub_raw)
    except ValueError as exc:
        raise ValidationError("Unsubscribe token user is not a UUID.") from exc

    if scope not in _VALID_SCOPES:
        raise ValidationError(f"Unsubscribe token scope is not recognised: {scope!r}")

    if int(_now()) - iat > _TOKEN_TTL_SECONDS:
        raise ValidationError("Unsubscribe token has expired.")

    if expected_scope is not None and scope != expected_scope:
        raise ValidationError(
            f"Unsubscribe token scope {scope!r} did not match "
            f"expected {expected_scope!r}."
        )

    return UnsubscribeToken(user_id=user_id, scope=scope, issued_at=iat)


def _signing_key() -> str:
    """Pick the active signing secret.

    Returns:
        The configured ``EMAIL_TOKEN_SECRET`` if non-empty, otherwise
        ``JWT_SECRET_KEY`` so dev environments don't require new
        secrets material.
    """
    settings = get_settings()
    secret = settings.email_token_secret or settings.jwt_secret_key
    return secret


def _sign(message: str, key: str) -> str:
    """HMAC-SHA256 sign ``message`` with ``key`` and return base64url.

    Args:
        message: The signing input (``header.payload``).
        key: The signing secret.

    Returns:
        URL-safe base64-encoded signature with trailing ``=`` removed.
    """
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(raw: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding.

    Args:
        raw: The raw bytes to encode.

    Returns:
        The encoded ASCII string with ``=`` padding stripped.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    """Decode a URL-safe base64 string that may have lost its padding.

    Args:
        value: Base64url-encoded ASCII string.

    Returns:
        The decoded raw bytes.
    """
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _now() -> float:
    """Return the current Unix timestamp.

    Wrapped so tests can monkey-patch wall-clock time without touching
    :mod:`time`.

    Returns:
        Current epoch time in seconds.
    """
    return time.time()
