"""HTTP routes for Google OAuth sign-in.

Two-step flow mirroring Spotify's:

1. ``GET /api/v1/auth/google/start`` — returns the Google consent URL
   and a signed ``state`` token the frontend must echo back.
2. ``POST /api/v1/auth/google/complete`` — accepts ``{code, state}``
   from the Google redirect, exchanges the code, upserts the user, and
   returns a Greenroom session JWT.

State is a short-lived JWT (10-minute expiry, ``purpose`` claim =
``google_oauth_state``) so the backend is free of Redis session
ceremony but still rejects forged redirects.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from flask import request

from backend.api.v1 import api_v1
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import GOOGLE_AUTH_FAILED, AppError, ValidationError
from backend.services import auth as auth_service
from backend.services import users as users_service

_STATE_PURPOSE = "google_oauth_state"
_STATE_TTL_SECONDS = 10 * 60
_STATE_ALGORITHM = "HS256"


@api_v1.route("/auth/google/start", methods=["GET"])
def google_start() -> tuple[dict[str, Any], int]:
    """Return the Google consent URL and a signed state token.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    state = _issue_state_token()
    return {
        "data": {
            "authorize_url": auth_service.google_build_authorize_url(state=state),
            "state": state,
        }
    }, 200


@api_v1.route("/auth/google/complete", methods=["POST"])
def google_complete() -> tuple[dict[str, Any], int]:
    """Finalize the Google OAuth flow and issue a Greenroom JWT.

    Request body: ``{"code": "...", "state": "..."}``.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing/malformed.
        AppError: ``GOOGLE_AUTH_FAILED`` when the service rejects the
            exchange or profile fetch.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    code = payload.get("code")
    state = payload.get("state")
    if not isinstance(code, str) or not code:
        raise ValidationError("Missing 'code' in request body.")
    if not isinstance(state, str) or not state:
        raise ValidationError("Missing 'state' in request body.")

    _verify_state_token(state)

    session = get_db()
    login = auth_service.google_complete(session, code=code)

    return {
        "data": {
            "token": login.jwt,
            "user": users_service.serialize_user(login.user),
        }
    }, 200


def _issue_state_token() -> str:
    """Mint a short-lived signed state token for the Google OAuth round trip.

    Returns:
        Encoded JWT valid for ``_STATE_TTL_SECONDS``.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "purpose": _STATE_PURPOSE,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_STATE_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_STATE_ALGORITHM)


def _verify_state_token(state: str) -> None:
    """Validate a Google OAuth state token, rejecting forgeries and replays.

    Args:
        state: State value Google echoed back on the redirect.

    Raises:
        AppError: ``GOOGLE_AUTH_FAILED`` if the state is expired, tampered
            with, or was not minted for this flow.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            state,
            settings.jwt_secret_key,
            algorithms=[_STATE_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise AppError(
            code=GOOGLE_AUTH_FAILED,
            message="Google OAuth state is invalid or expired.",
            status_code=400,
        ) from exc
    if claims.get("purpose") != _STATE_PURPOSE:
        raise AppError(
            code=GOOGLE_AUTH_FAILED,
            message="Google OAuth state has the wrong purpose.",
            status_code=400,
        )
