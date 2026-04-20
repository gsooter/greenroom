"""Authenticated user route handlers.

All endpoints here require a valid JWT via the ``@require_auth``
decorator and operate on the caller's own profile (``/me``). No
admin-style "fetch another user by ID" endpoint is exposed from v1.
"""

from __future__ import annotations

import contextlib
from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.services import spotify as spotify_service
from backend.services import users as users_service


@api_v1.route("/me", methods=["GET"])
@require_auth
def get_me() -> tuple[dict[str, Any], int]:
    """Return the authenticated user's profile.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    user = get_current_user()
    return {"data": users_service.serialize_user(user)}, 200


@api_v1.route("/me", methods=["PATCH"])
@require_auth
def update_me() -> tuple[dict[str, Any], int]:
    """Apply a partial update to the authenticated user's profile.

    Patchable fields: ``display_name``, ``city_id``, ``digest_frequency``,
    ``genre_preferences``, ``notification_settings``. Any other field in
    the body is ignored.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        ValidationError: If the request body is not a JSON object or any
            field value fails validation.
        NotFoundError: If ``city_id`` references a nonexistent city.
    """
    session = get_db()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    user = get_current_user()
    updated = users_service.update_user_profile(session, user, payload)
    return {"data": users_service.serialize_user(updated)}, 200


@api_v1.route("/me/spotify/top-artists", methods=["GET"])
@require_auth
def get_my_top_artists() -> tuple[dict[str, Any], int]:
    """Return the authenticated user's cached Spotify top-artists snapshot.

    If the user has no snapshot yet (unusual — inline sync runs on
    login), attempts a live refresh before giving up. On Spotify
    errors returns an empty list so the UI can degrade gracefully.

    Returns:
        Tuple of JSON body (``{data: {artists: [...], synced_at}}``)
        and HTTP 200 status code.
    """
    session = get_db()
    user = get_current_user()

    if not user.spotify_top_artists:
        # Degrade, don't blow up UI if Spotify sync fails.
        with contextlib.suppress(Exception):
            spotify_service.sync_top_artists(session, user)

    return {
        "data": {
            "artists": user.spotify_top_artists or [],
            "synced_at": (
                user.spotify_synced_at.isoformat() if user.spotify_synced_at else None
            ),
        }
    }, 200


@api_v1.route("/me/music-connections", methods=["GET"])
@require_auth
def get_my_music_connections() -> tuple[dict[str, Any], int]:
    """Return the caller's music-service connection state.

    Settings reads this once on load to render a card per provider —
    "connected | last synced | N artists" — so Tidal and Apple Music
    reflect backend truth instead of transient client flags.

    Returns:
        Tuple of JSON body ``{data: {connections: [...]}}`` and 200.
    """
    user = get_current_user()
    return {"data": {"connections": users_service.list_music_connections(user)}}, 200


@api_v1.route("/me", methods=["DELETE"])
@require_auth
def delete_me() -> tuple[dict[str, Any], int]:
    """Soft-delete the authenticated user's account.

    Sets ``is_active=False`` so downstream ``@require_auth`` requests
    with the same token are rejected. Saved events and recommendations
    are preserved so analytics and reactivation remain possible.

    Returns:
        Tuple of empty JSON body and HTTP 204 status code.
    """
    session = get_db()
    users_service.deactivate_user(session, get_current_user())
    return {}, 204
