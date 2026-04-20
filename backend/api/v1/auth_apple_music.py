"""Apple Music connect route handlers — skeleton.

Unlike Spotify and Tidal (OAuth redirect), Apple Music uses MusicKit
JS in the browser. The server's job is to:

1. Mint a short-lived developer token on demand so MusicKit JS can
   authenticate against Apple's API.
2. Accept a Music User Token (MUT) the browser obtains after the user
   clicks "Allow" on Apple's MusicKit prompt, validate it, and persist
   it as a :class:`MusicServiceConnection` row.

Both endpoints sit behind :func:`require_auth` — Apple Music is a
*connect* flow, not a sign-in. In environments without Apple Developer
credentials (``APPLE_MUSIC_*`` env vars empty),
:func:`apple_music_service.is_configured` returns False and every call
here surfaces a clear 503 instead of signing a JWT with a bogus key.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import (
    APPLE_MUSIC_AUTH_FAILED,
    AppError,
    ValidationError,
)
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider
from backend.data.repositories import users as users_repo
from backend.services import apple_music as apple_music_service
from backend.services import users as users_service

logger = get_logger(__name__)


@api_v1.route("/auth/apple-music/developer-token", methods=["GET"])
@require_auth
def apple_music_developer_token() -> tuple[dict[str, Any], int]:
    """Return a fresh MusicKit JS developer token.

    Returns:
        Tuple of JSON body ``{"developer_token": "..."}`` and 200.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` (503) if the Apple
            Developer credentials have not been populated yet.
    """
    token = apple_music_service.mint_developer_token()
    return {"data": {"developer_token": token}}, 200


@api_v1.route("/auth/apple-music/connect", methods=["POST"])
@require_auth
def apple_music_connect() -> tuple[dict[str, Any], int]:
    """Link the caller's Apple Music account using a Music User Token.

    Request body: ``{"music_user_token": "..."}``.

    Validates the MUT against Apple's API, upserts the
    :class:`MusicServiceConnection` row, and returns the refreshed
    user profile.

    Returns:
        Tuple of JSON body ``{"user": {...}}`` and 200.

    Raises:
        ValidationError: If the body is not valid JSON or missing
            ``music_user_token``.
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` if the MUT is rejected,
            if credentials are not configured, or if the profile is
            already linked to a different Greenroom user.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    mut = payload.get("music_user_token")
    if not isinstance(mut, str) or not mut:
        raise ValidationError("Missing 'music_user_token' in request body.")

    identity = apple_music_service.validate_music_user_token(mut)

    session = get_db()
    user = get_current_user()

    existing = users_repo.get_music_connection(
        session,
        provider=OAuthProvider.APPLE_MUSIC,
        provider_user_id=identity.provider_user_id,
    )
    if existing is not None and existing.user_id != user.id:
        raise AppError(
            code=APPLE_MUSIC_AUTH_FAILED,
            message="Apple Music account is already linked to another user.",
            status_code=409,
        )
    if existing is not None:
        users_repo.update_music_connection_tokens(
            session,
            existing,
            access_token=mut,
            refresh_token=None,
            token_expires_at=None,
        )
    else:
        users_repo.create_music_connection(
            session,
            user_id=user.id,
            provider=OAuthProvider.APPLE_MUSIC,
            provider_user_id=identity.provider_user_id,
            access_token=mut,
            refresh_token=None,
            token_expires_at=None,
            scopes=None,
        )

    try:
        apple_music_service.sync_top_artists(session, user, music_user_token=mut)
    except AppError as exc:
        logger.warning(
            "Inline Apple Music top-artists sync failed for user %s: %s",
            user.id,
            exc,
        )

    return {"data": {"user": users_service.serialize_user(user)}}, 200
