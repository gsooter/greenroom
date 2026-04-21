"""Greenroom-side proxy routes for Knuckles identity ceremonies.

After Decision 030 every access token is issued by Knuckles. Greenroom
does not sit on the consent path for magic-link, Google, Apple, or
passkey sign-in, but Knuckles endpoints require confidential-client
credentials (``X-Client-Id`` / ``X-Client-Secret`` per Knuckles
Decision 007). Rather than ship those credentials to the browser, we
proxy the ceremonies through Greenroom so the secret stays in the
server-side environment.

Each route here forwards to the matching Knuckles endpoint via
:func:`backend.core.knuckles_client.post`, fills in the
``redirect_url`` from :data:`settings.frontend_base_url`, and — for
the sign-in-completing halves — decodes the returned access token to
lazily provision the Greenroom ``users`` row and return a normalized
``{token, user}`` envelope the frontend's AuthContext already consumes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import require_auth
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import (
    INVALID_TOKEN,
    AppError,
    UnauthorizedError,
    ValidationError,
)
from backend.core.knuckles_client import post as knuckles_post
from backend.core.knuckles_client import verify_knuckles_token
from backend.core.logging import get_logger
from backend.core.rate_limit import rate_limit
from backend.data.repositories import users as users_repo
from backend.services import users as users_service

if TYPE_CHECKING:
    from backend.data.models.users import User

logger = get_logger(__name__)

_MAGIC_LINK_REDIRECT_PATH = "/auth/verify"
_GOOGLE_REDIRECT_PATH = "/auth/google/callback"
_APPLE_REDIRECT_PATH = "/auth/apple/callback"


# ---------------------------------------------------------------------------
# Magic link
# ---------------------------------------------------------------------------


def _magic_link_email_key() -> str:
    """Rate-limit key extractor for per-email magic-link throttling.

    Returns:
        A normalized email address, or the empty string when the body
        is missing or malformed — in that case the limiter skips and
        the route's own validation will reject with 422.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return ""
    value = body.get("email")
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


@api_v1.route("/auth/magic-link/request", methods=["POST"])
@rate_limit("magic_link_request_ip", limit=10, window_seconds=3600)
@rate_limit(
    "magic_link_request_email",
    limit=5,
    window_seconds=3600,
    key_fn=_magic_link_email_key,
)
def magic_link_request() -> tuple[dict[str, Any], int]:
    """Proxy a magic-link send request to Knuckles.

    Returns:
        Tuple of JSON body (``email_sent: True``) and HTTP 200. The
        response is intentionally identical whether or not the email
        is registered, so the client can't enumerate accounts.

    Raises:
        ValidationError: If the body is missing or ``email`` is absent.
    """
    email = _require_string(request.get_json(silent=True), "email")
    knuckles_post(
        "/v1/auth/magic-link/start",
        json={
            "email": email,
            "redirect_url": _frontend_url(_MAGIC_LINK_REDIRECT_PATH),
        },
    )
    return {"data": {"email_sent": True}}, 200


@api_v1.route("/auth/magic-link/verify", methods=["POST"])
@rate_limit("magic_link_verify_ip", limit=20, window_seconds=60)
def magic_link_verify() -> tuple[dict[str, Any], int]:
    """Redeem a magic-link token and return ``{token, user}``.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200. The
        ``token`` is the Knuckles-issued access token; the ``user`` is
        the Greenroom profile (lazily created on first sign-in).

    Raises:
        ValidationError: If the body is missing ``token``.
        AppError: Propagated from Knuckles or from token verification
            if Knuckles returns an unusable response.
    """
    token = _require_string(request.get_json(silent=True), "token")
    response = knuckles_post("/v1/auth/magic-link/verify", json={"token": token})
    return {"data": _exchange_session(response)}, 200


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@api_v1.route("/auth/google/start", methods=["GET"])
@rate_limit("oauth_start_ip", limit=30, window_seconds=60)
def google_start() -> tuple[dict[str, Any], int]:
    """Return a Google consent URL and signed state token from Knuckles.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    response = knuckles_post(
        "/v1/auth/google/start",
        json={"redirect_url": _frontend_url(_GOOGLE_REDIRECT_PATH)},
    )
    return {"data": _passthrough_data(response)}, 200


@api_v1.route("/auth/google/complete", methods=["POST"])
@rate_limit("oauth_complete_ip", limit=20, window_seconds=60)
def google_complete() -> tuple[dict[str, Any], int]:
    """Exchange a Google code/state pair for a Greenroom session.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If the body is missing ``code`` or ``state``.
        AppError: Propagated from Knuckles if the exchange fails.
    """
    body = request.get_json(silent=True)
    code = _require_string(body, "code")
    state = _require_string(body, "state")
    response = knuckles_post(
        "/v1/auth/google/complete",
        json={"code": code, "state": state},
    )
    return {"data": _exchange_session(response)}, 200


# ---------------------------------------------------------------------------
# Apple OAuth
# ---------------------------------------------------------------------------


@api_v1.route("/auth/apple/start", methods=["GET"])
@rate_limit("oauth_start_ip", limit=30, window_seconds=60)
def apple_start() -> tuple[dict[str, Any], int]:
    """Return an Apple consent URL and signed state token from Knuckles.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    response = knuckles_post(
        "/v1/auth/apple/start",
        json={"redirect_url": _frontend_url(_APPLE_REDIRECT_PATH)},
    )
    return {"data": _passthrough_data(response)}, 200


@api_v1.route("/auth/apple/complete", methods=["POST"])
@rate_limit("oauth_complete_ip", limit=20, window_seconds=60)
def apple_complete() -> tuple[dict[str, Any], int]:
    """Exchange an Apple code/state (plus optional user blob) for a session.

    Apple only POSTs the ``user`` payload on the very first sign-in for
    a given Apple ID, so the frontend forwards it verbatim when present.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing, or ``user``
            is present but not an object.
        AppError: Propagated from Knuckles if the exchange fails.
    """
    body = request.get_json(silent=True)
    code = _require_string(body, "code")
    state = _require_string(body, "state")
    user_data = body.get("user") if isinstance(body, dict) else None
    if user_data is not None and not isinstance(user_data, dict):
        raise ValidationError("'user' must be an object when provided.")
    response = knuckles_post(
        "/v1/auth/apple/complete",
        json={"code": code, "state": state, "user": user_data},
    )
    return {"data": _exchange_session(response)}, 200


# ---------------------------------------------------------------------------
# Passkey — registration (authenticated)
# ---------------------------------------------------------------------------


@api_v1.route("/auth/passkey/register/start", methods=["POST"])
@require_auth
def passkey_register_start() -> tuple[dict[str, Any], int]:
    """Begin a WebAuthn registration ceremony for the signed-in caller.

    The caller's bearer token is forwarded to Knuckles, which owns the
    ``webauthn_credentials`` table and the challenge-state JWT.

    Returns:
        Tuple of JSON body (``options``, ``state``) and HTTP 200.
    """
    bearer = _extract_bearer()
    response = knuckles_post(
        "/v1/auth/passkey/register/begin",
        bearer_token=bearer,
    )
    return {"data": _passthrough_data(response)}, 200


@api_v1.route("/auth/passkey/register/complete", methods=["POST"])
@require_auth
def passkey_register_complete() -> tuple[dict[str, Any], int]:
    """Verify a passkey attestation and persist it on Knuckles.

    Returns:
        Tuple of JSON body (``registered: True``) and HTTP 200. The
        Knuckles response carries a ``credential_id`` we drop — the
        frontend only needs a success signal.

    Raises:
        ValidationError: If ``credential`` or ``state`` is missing.
        AppError: Propagated from Knuckles if verification fails.
    """
    body = request.get_json(silent=True)
    credential = _require_object(body, "credential")
    state = _require_string(body, "state")
    name = body.get("name") if isinstance(body, dict) else None
    knuckles_post(
        "/v1/auth/passkey/register/complete",
        json={
            "credential": credential,
            "state": state,
            "name": name if isinstance(name, str) and name else None,
        },
        bearer_token=_extract_bearer(),
    )
    return {"data": {"registered": True}}, 200


# ---------------------------------------------------------------------------
# Passkey — sign-in (anonymous)
# ---------------------------------------------------------------------------


@api_v1.route("/auth/passkey/authenticate/start", methods=["POST"])
@rate_limit("passkey_auth_start_ip", limit=30, window_seconds=60)
def passkey_authenticate_start() -> tuple[dict[str, Any], int]:
    """Return a discoverable-credential authentication challenge.

    Returns:
        Tuple of JSON body (``options``, ``state``) and HTTP 200.
    """
    response = knuckles_post("/v1/auth/passkey/sign-in/begin")
    return {"data": _passthrough_data(response)}, 200


@api_v1.route("/auth/passkey/authenticate/complete", methods=["POST"])
@rate_limit("passkey_auth_complete_ip", limit=20, window_seconds=60)
def passkey_authenticate_complete() -> tuple[dict[str, Any], int]:
    """Verify a passkey assertion and mint a Greenroom session.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``credential`` or ``state`` is missing.
        AppError: Propagated from Knuckles if verification fails.
    """
    body = request.get_json(silent=True)
    credential = _require_object(body, "credential")
    state = _require_string(body, "state")
    response = knuckles_post(
        "/v1/auth/passkey/sign-in/complete",
        json={"credential": credential, "state": state},
    )
    return {"data": _exchange_session(response)}, 200


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@api_v1.route("/auth/refresh", methods=["POST"])
@rate_limit("refresh_ip", limit=60, window_seconds=60)
def refresh_session() -> tuple[dict[str, Any], int]:
    """Rotate a refresh token into a fresh access+refresh pair.

    The Knuckles refresh endpoint rotates the refresh token (single-use
    by design), so the frontend must replace both stored tokens from
    this response. This path does not bump ``last_login_at`` — refresh
    is a silent renewal, not a fresh sign-in.

    Returns:
        Tuple of JSON body (``token``, ``token_expires_at``,
        ``refresh_token``, ``refresh_token_expires_at``, ``user``) and
        HTTP 200.

    Raises:
        ValidationError: If the body is missing ``refresh_token``.
        AppError: Propagated from Knuckles if the token is invalid,
            expired, reused, or belongs to a different client.
    """
    refresh_token = _require_string(request.get_json(silent=True), "refresh_token")
    response = knuckles_post(
        "/v1/token/refresh",
        json={"refresh_token": refresh_token},
    )
    return {"data": _session_envelope(response, bump_last_login=False)}, 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frontend_url(path: str) -> str:
    """Build an absolute Greenroom frontend URL for the given path.

    Args:
        path: Path on the frontend (starting with ``/``).

    Returns:
        Absolute URL using :attr:`Settings.frontend_base_url`.
    """
    return get_settings().frontend_base_url.rstrip("/") + path


def _require_string(body: Any, field: str) -> str:
    """Pull a non-empty string field from a JSON body or raise.

    Args:
        body: Parsed JSON body, expected to be a dict.
        field: Key to require on the body.

    Returns:
        The validated non-empty string.

    Raises:
        ValidationError: If the body is not an object or the field is
            absent, null, or non-string.
    """
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Missing '{field}' in request body.")
    return value


def _require_object(body: Any, field: str) -> dict[str, Any]:
    """Pull a dict-shaped field from a JSON body or raise.

    Args:
        body: Parsed JSON body, expected to be a dict.
        field: Key to require on the body.

    Returns:
        The validated dict.

    Raises:
        ValidationError: If the body is not an object or the field is
            absent or not a dict.
    """
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    value = body.get(field)
    if not isinstance(value, dict):
        raise ValidationError(f"Missing '{field}' in request body.")
    return value


def _extract_bearer() -> str:
    """Pull the raw bearer token off the current request's Authorization header.

    Returns:
        The token string.

    Raises:
        UnauthorizedError: If no bearer header is present. The surrounding
            ``@require_auth`` already catches this case, so this is a
            defense-in-depth fallback for future refactors.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise UnauthorizedError(message="Missing or malformed Authorization header.")
    token = header[len("bearer ") :].strip()
    if not token:
        raise UnauthorizedError(message="Bearer token is empty.")
    return token


def _passthrough_data(response: dict[str, Any]) -> dict[str, Any]:
    """Unwrap Knuckles' ``{"data": {...}}`` envelope.

    Args:
        response: Decoded Knuckles JSON response.

    Returns:
        The inner ``data`` dict, or an empty dict if missing.
    """
    data = response.get("data") if isinstance(response, dict) else None
    return data if isinstance(data, dict) else {}


def _exchange_session(response: dict[str, Any]) -> dict[str, Any]:
    """Turn a Knuckles sign-in response into a fresh-login session envelope.

    Used by the ceremony-completing proxies (magic-link verify,
    Google/Apple complete, passkey authenticate complete). Bumps
    ``last_login_at`` because these paths represent a user actively
    signing in rather than a background refresh.

    Args:
        response: Full Knuckles response body (``{"data": {...}}``).

    Returns:
        Dict with ``token``, ``token_expires_at``, ``refresh_token``,
        ``refresh_token_expires_at``, and ``user``.

    Raises:
        AppError: ``INVALID_TOKEN`` (502) if Knuckles returns a
            malformed response. ``INVALID_TOKEN`` (401) if the token's
            claims are missing fields Greenroom needs to provision.
    """
    return _session_envelope(response, bump_last_login=True)


def _session_envelope(
    response: dict[str, Any], *, bump_last_login: bool
) -> dict[str, Any]:
    """Build the normalized ``{token, refresh_token, user, ...}`` envelope.

    Args:
        response: Full Knuckles response body (``{"data": {...}}``).
        bump_last_login: If True, stamp ``users.last_login_at`` with
            now. Sign-in ceremonies set this; token refresh does not
            (refresh is a silent renewal, not an activity signal).

    Returns:
        Dict with ``token``, ``token_expires_at``, ``refresh_token``,
        ``refresh_token_expires_at``, and ``user``. Expiry fields
        pass through whatever Knuckles sent (``None`` when absent).

    Raises:
        AppError: ``INVALID_TOKEN`` (502) if Knuckles returned no
            access token. ``INVALID_TOKEN`` (401) on claim-validation
            failures.
    """
    data = _passthrough_data(response)
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise AppError(
            code=INVALID_TOKEN,
            message="Identity service returned no access token.",
            status_code=502,
        )
    user = _resolve_user(access_token)
    if bump_last_login:
        users_repo.update_last_login(get_db(), user)
    return {
        "token": access_token,
        "token_expires_at": data.get("access_token_expires_at"),
        "refresh_token": data.get("refresh_token"),
        "refresh_token_expires_at": data.get("refresh_token_expires_at"),
        "user": users_service.serialize_user(user),
    }


def _resolve_user(access_token: str) -> User:
    """Verify a Knuckles access token and return the Greenroom user.

    Decodes claims against the cached JWKS, loads the user by ``sub``,
    or lazily creates a Greenroom ``users`` row from the token claims
    when this is the user's first authenticated hit.

    Args:
        access_token: The Knuckles-issued access token to verify.

    Returns:
        The resolved :class:`User` — either freshly looked up or
        freshly provisioned from claims.

    Raises:
        AppError: ``INVALID_TOKEN`` (401) if the token is unverifiable
            or the claim shape is unusable for provisioning.
    """
    claims = verify_knuckles_token(access_token)
    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token is missing a subject.",
            status_code=401,
        )
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token subject is not a valid UUID.",
            status_code=401,
        ) from exc

    session = get_db()
    user = users_repo.get_user_by_id(session, user_id)
    if user is None:
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise AppError(
                code=INVALID_TOKEN,
                message="Access token is missing an email claim.",
                status_code=401,
            )
        existing = users_repo.get_user_by_email(session, email)
        if existing is not None:
            raise AppError(
                code=INVALID_TOKEN,
                message=(
                    "A Greenroom user with this email already exists under a "
                    "different id. Reconcile the legacy row before signing in."
                ),
                status_code=409,
            )
        display_name = claims.get("name")
        return users_repo.create_user(
            session,
            user_id=user_id,
            email=email,
            display_name=display_name if isinstance(display_name, str) else None,
        )
    if not user.is_active:
        # A soft-deleted account that completes a fresh Knuckles exchange
        # is a returning user — restore the row instead of dead-ending
        # them on a message they can't act on. Deactivation is a pause,
        # not a tombstone; every downstream row is still intact.
        user = users_service.reactivate_user(session, user)
    return user
