"""HTTP routes for Sign-in-with-Apple.

Apple's redirect is an HTTP POST (``response_mode=form_post``) back to
the frontend, which reads the form, stashes any ``user`` payload, and
then calls :http:post:`/api/v1/auth/apple/complete` with JSON.

State rides as a short-lived signed JWT — same pattern as Spotify and
Google.
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
from backend.core.exceptions import APPLE_AUTH_FAILED, AppError, ValidationError
from backend.services import auth as auth_service
from backend.services import users as users_service

_STATE_PURPOSE = "apple_oauth_state"
_STATE_TTL_SECONDS = 10 * 60
_STATE_ALGORITHM = "HS256"


@api_v1.route("/auth/apple/start", methods=["GET"])
def apple_start() -> tuple[dict[str, Any], int]:
    """Return the Apple consent URL and a signed state token.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    state = _issue_state_token()
    return {
        "data": {
            "authorize_url": auth_service.apple_build_authorize_url(state=state),
            "state": state,
        }
    }, 200


@api_v1.route("/auth/apple/complete", methods=["POST"])
def apple_complete() -> tuple[dict[str, Any], int]:
    """Finalize the Apple sign-in flow and issue a Greenroom JWT.

    Request body: ``{"code": "...", "state": "...", "user": {...}?}``.

    The ``user`` field is optional and only present on the first
    sign-in; it carries the user's display name since the id_token
    does not.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing/malformed.
        AppError: ``APPLE_AUTH_FAILED`` when the service rejects the
            exchange or id-token verification.
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

    user_data = payload.get("user")
    if user_data is not None and not isinstance(user_data, dict):
        raise ValidationError("'user' must be a JSON object when supplied.")

    session = get_db()
    login = auth_service.apple_complete(session, code=code, user_data=user_data)

    return {
        "data": {
            "token": login.jwt,
            "user": users_service.serialize_user(login.user),
        }
    }, 200


def _issue_state_token() -> str:
    """Mint a short-lived signed state token for the Apple OAuth round trip.

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
    """Validate an Apple OAuth state token, rejecting forgeries and replays.

    Args:
        state: State value Apple echoed back on the redirect.

    Raises:
        AppError: ``APPLE_AUTH_FAILED`` if the state is expired, tampered
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
            code=APPLE_AUTH_FAILED,
            message="Apple OAuth state is invalid or expired.",
            status_code=400,
        ) from exc
    if claims.get("purpose") != _STATE_PURPOSE:
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple OAuth state has the wrong purpose.",
            status_code=400,
        )
