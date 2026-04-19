"""Provider-agnostic session endpoints.

``/auth/me`` and ``/auth/logout`` work the same regardless of how the
caller originally authenticated (magic link, Google, Apple, passkey,
or Spotify). They live here rather than next to the OAuth-specific
routes so the frontend has stable, login-method-independent paths.
"""

from __future__ import annotations

from typing import Any

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.services import users as users_service


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
    """Acknowledge a client-driven logout.

    JWTs are stateless so the server does not maintain a session table
    — the client drops the token from storage. This endpoint exists so
    the frontend has a single place to call during logout and so we
    can hook audit logging or token denylisting here later without a
    client-side contract change.

    Returns:
        Tuple of empty JSON body and HTTP 204 status code.
    """
    return {}, 204
