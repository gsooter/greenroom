"""Provider-agnostic session endpoints.

``/auth/me`` and ``/auth/logout`` work the same regardless of how the
caller originally authenticated (magic link, Google, Apple, passkey,
or Spotify). They live here rather than next to the OAuth-specific
routes so the frontend has stable, login-method-independent paths.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.exceptions import AppError
from backend.core.knuckles_client import post as knuckles_post
from backend.core.logging import get_logger
from backend.services import users as users_service

logger = get_logger(__name__)


@api_v1.route("/auth/me", methods=["GET"])
@require_auth
def auth_me() -> tuple[dict[str, Any], int]:
    """Return the authenticated user's serialized profile.

    A thin alias for ``/me`` placed under ``/auth`` so the frontend's
    AuthContext can hit a single path during bootstrap regardless of
    which sign-in method minted the JWT.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    return {"data": users_service.serialize_user(get_current_user())}, 200


@api_v1.route("/auth/logout", methods=["POST"])
@require_auth
def auth_logout() -> tuple[dict[str, Any], int]:
    """Revoke the caller's refresh token on Knuckles and confirm logout.

    If the request body carries a ``refresh_token`` string, forward it
    to Knuckles ``POST /v1/logout`` so the refresh token family is
    invalidated server-side. The access token itself is stateless —
    the frontend still drops it locally — but killing the refresh
    token prevents silent session resurrection from stolen storage.

    Knuckles treats unknown tokens as a no-op and returns 204, and
    we mirror that shape: any forwarding failure is logged and
    swallowed so the client contract is "logout always succeeds."

    Returns:
        Tuple of empty JSON body and HTTP 204 status code.
    """
    body = request.get_json(silent=True)
    refresh_token = body.get("refresh_token") if isinstance(body, dict) else None
    if isinstance(refresh_token, str) and refresh_token:
        try:
            knuckles_post("/v1/logout", json={"refresh_token": refresh_token})
        except AppError as exc:
            logger.warning(
                "knuckles_logout_forwarding_failed",
                extra={"status_code": exc.status_code, "code": exc.code},
            )
    return {}, 204
