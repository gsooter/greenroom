"""Provider-agnostic session endpoints.

``/auth/me`` and ``/auth/logout`` work the same regardless of how the
caller originally authenticated (magic link, Google, Apple, passkey,
or Spotify). They live here rather than next to the OAuth-specific
routes so the frontend has stable, login-method-independent paths.
"""

from __future__ import annotations

from typing import Any

from flask import request
from knuckles_client.exceptions import KnucklesError

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.knuckles import get_client
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

    If the request body carries a ``refresh_token`` string, hand it to
    :meth:`KnucklesClient.logout` so the refresh token family is
    invalidated server-side. The access token itself is stateless —
    the frontend still drops it locally — but killing the refresh
    token prevents silent session resurrection from stolen storage.

    The SDK swallows unknown/expired tokens (logout is idempotent), so
    we only catch the broader :class:`KnucklesError` for true transport
    failures and log them; the client contract is "logout always
    succeeds."

    Returns:
        Tuple of empty JSON body and HTTP 204 status code.
    """
    body = request.get_json(silent=True)
    refresh_token = body.get("refresh_token") if isinstance(body, dict) else None
    if isinstance(refresh_token, str) and refresh_token:
        try:
            get_client().logout(refresh_token)
        except KnucklesError as exc:
            logger.warning(
                "knuckles_logout_forwarding_failed",
                extra={"error": str(exc)},
            )
    return {}, 204
