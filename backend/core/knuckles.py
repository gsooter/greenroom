"""Process-wide Knuckles SDK glue.

Owns the singleton :class:`knuckles_client.KnucklesClient` and a thin
:func:`verify_knuckles_token` shim that translates the SDK's
:class:`KnucklesTokenError` into Greenroom's :class:`AppError` so the
auth decorator and route handlers continue to surface the codes the
HTTP error envelope expects (``INVALID_TOKEN`` / ``TOKEN_EXPIRED``).

Every Knuckles call (magic-link, Google, Apple, passkey, refresh,
logout, JWT verify) goes through :func:`get_client` so credentials
stay loaded once and the JWKS verifier's in-memory cache is shared.
"""

from __future__ import annotations

import threading
from typing import Any

import jwt
from knuckles_client import KnucklesClient
from knuckles_client.exceptions import (
    KnucklesAuthError,
    KnucklesTokenError,
)

from backend.core.config import get_settings
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
)
from backend.core.logging import get_logger

logger = get_logger(__name__)

_client: KnucklesClient | None = None
_client_lock = threading.Lock()


def get_client() -> KnucklesClient:
    """Return the process-wide :class:`KnucklesClient`, building it lazily.

    The SDK is thread-safe — its transport sits on a
    :class:`requests.Session` connection pool and the JWKS verifier's
    cache is read-mostly. One instance per process is plenty.

    Returns:
        The shared :class:`KnucklesClient`.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                settings = get_settings()
                client = KnucklesClient(
                    base_url=settings.knuckles_url,
                    client_id=settings.knuckles_client_id,
                    client_secret=settings.knuckles_client_secret,
                )
                # Knuckles' WAF returns 403 to the `Python-urllib/*` UA that
                # PyJWKClient sends by default, breaking JWKS fetches for
                # every token verification. Swap in a PyJWKClient with a
                # neutral UA until the WAF whitelists the JWKS path.
                verifier = client._verifier
                verifier._jwks = jwt.PyJWKClient(
                    verifier.jwks_uri,
                    cache_keys=True,
                    headers={"User-Agent": "greenroom-backend"},
                )
                _client = client
    return _client


def reset_client() -> None:
    """Drop the cached :class:`KnucklesClient` singleton.

    Tests that swap the Knuckles base URL, client id, or JWKS endpoint
    between cases call this to force a rebuild on the next access.
    """
    global _client
    with _client_lock:
        _client = None


def verify_knuckles_token(token: str) -> dict[str, Any]:
    """Verify a Knuckles RS256 access token and return its claims.

    Delegates to :meth:`KnucklesClient.verify_access_token` and maps the
    SDK's :class:`KnucklesTokenError` into Greenroom's
    :class:`AppError` envelope. Expiry surfaces as ``TOKEN_EXPIRED`` so
    the frontend can attempt a silent refresh; everything else surfaces
    as ``INVALID_TOKEN`` so callers don't need to pattern-match strings.

    Args:
        token: The raw bearer-token value from an ``Authorization``
            header.

    Returns:
        The decoded claims dictionary.

    Raises:
        AppError: ``TOKEN_EXPIRED`` (401) on an expired ``exp`` claim.
            ``INVALID_TOKEN`` (401) for any other verification failure.
    """
    try:
        return get_client().verify_access_token(token)
    except KnucklesTokenError as exc:
        if isinstance(exc.__cause__, jwt.ExpiredSignatureError):
            raise AppError(
                code=TOKEN_EXPIRED,
                message="Access token has expired.",
                status_code=401,
            ) from exc
        cause = exc.__cause__
        settings = get_settings()
        logger.warning(
            "knuckles_token_verify_failed sdk_message=%r cause_type=%s "
            "cause_message=%r expected_issuer=%r expected_audience=%r",
            str(exc),
            type(cause).__name__ if cause is not None else None,
            str(cause) if cause is not None else None,
            settings.knuckles_url.rstrip("/"),
            settings.knuckles_client_id,
        )
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token is invalid.",
            status_code=401,
        ) from exc


def auth_error_to_app_error(exc: KnucklesAuthError) -> AppError:
    """Map an SDK :class:`KnucklesAuthError` into Greenroom's envelope.

    Used by ceremony-completing routes that want to surface the SDK's
    HTTP status verbatim rather than collapse everything to 401. The
    SDK's ``code`` attribute (``REFRESH_TOKEN_REUSED``,
    ``REFRESH_TOKEN_EXPIRED``, ``GOOGLE_AUTH_FAILED``, etc.) is passed
    through as-is so the frontend's error matching keeps working.

    Args:
        exc: The SDK exception caught by the route handler.

    Returns:
        An :class:`AppError` whose code, message, and status mirror the
        SDK exception.
    """
    return AppError(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


__all__ = [
    "auth_error_to_app_error",
    "get_client",
    "reset_client",
    "verify_knuckles_token",
]
