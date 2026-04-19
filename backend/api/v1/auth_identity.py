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
from typing import Any

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
from backend.data.repositories import users as users_repo
from backend.services import users as users_service

logger = get_logger(__name__)

_MAGIC_LINK_REDIRECT_PATH = "/auth/verify"
_GOOGLE_REDIRECT_PATH = "/auth/google/callback"
_APPLE_REDIRECT_PATH = "/auth/apple/callback"


# ---------------------------------------------------------------------------
# Magic link
# ---------------------------------------------------------------------------


@api_v1.route("/auth/magic-link/request", methods=["POST"])
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
def passkey_authenticate_start() -> tuple[dict[str, Any], int]:
    """Return a discoverable-credential authentication challenge.

    Returns:
        Tuple of JSON body (``options``, ``state``) and HTTP 200.
    """
    response = knuckles_post("/v1/auth/passkey/sign-in/begin")
    return {"data": _passthrough_data(response)}, 200


@api_v1.route("/auth/passkey/authenticate/complete", methods=["POST"])
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
    """Turn a Knuckles token-pair response into ``{token, user}``.

    Verifies the returned access token against the Knuckles JWKS (the
    same path :func:`require_auth` uses for every subsequent request),
    then loads or lazily provisions the Greenroom user row keyed by
    the ``sub`` claim, and returns the normalized envelope the
    frontend's AuthContext already consumes.

    Args:
        response: Full Knuckles response body (``{"data": {...}}``).

    Returns:
        Dict with ``token`` (the Knuckles access token) and ``user``
        (the serialized Greenroom profile).

    Raises:
        AppError: ``INVALID_TOKEN`` (502) if Knuckles returns a
            malformed or unverifiable response. ``INVALID_TOKEN`` (401)
            if the token's claims are missing the fields Greenroom
            needs to provision a user.
        UnauthorizedError: If the resolved user has been deactivated.
    """
    data = _passthrough_data(response)
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise AppError(
            code=INVALID_TOKEN,
            message="Identity service returned no access token.",
            status_code=502,
        )
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
        display_name = claims.get("name")
        user = users_repo.create_user(
            session,
            user_id=user_id,
            email=email,
            display_name=display_name if isinstance(display_name, str) else None,
        )
    elif not user.is_active:
        raise UnauthorizedError(message="Authenticated user is deactivated.")

    users_repo.update_last_login(session, user)
    return {
        "token": access_token,
        "user": users_service.serialize_user(user),
    }
