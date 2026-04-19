"""Spotify OAuth route handlers.

Two-step flow, both halves require a valid Knuckles access token — the
user has already signed in via Knuckles and is now *connecting* their
Spotify account to their Greenroom profile:

1. ``GET /api/v1/auth/spotify/start`` — returns the Spotify consent URL
   and a signed ``state`` token. The client performs a full-page
   navigation to that URL.

2. ``POST /api/v1/auth/spotify/complete`` — called by the frontend
   callback page with the ``code`` and ``state`` Spotify appended to
   the redirect. Exchanges the code for tokens and upserts the
   MusicServiceConnection row attached to the caller's user. Greenroom
   issues no tokens of its own (Decision 030); the caller keeps using
   their Knuckles access token.

State is carried as a short-lived JWT (10-minute expiry, ``purpose``
claim = ``spotify_oauth_state``) rather than a Redis entry. That keeps
the auth path free of Redis connection ceremony and makes the state
self-verifying; it still prevents CSRF because the attacker can't
forge a JWT signed with our secret.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt
from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import (
    SPOTIFY_AUTH_FAILED,
    AppError,
    ValidationError,
)
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider, User
from backend.data.repositories import users as users_repo
from backend.services import spotify as spotify_service
from backend.services import users as users_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

_STATE_PURPOSE = "spotify_oauth_state"
_STATE_TTL_SECONDS = 10 * 60
_STATE_ALGORITHM = "HS256"


@api_v1.route("/auth/spotify/start", methods=["GET"])
@require_auth
def spotify_start() -> tuple[dict[str, Any], int]:
    """Return the Spotify consent URL and a signed state token.

    The frontend navigates the browser to ``authorize_url`` and later
    POSTs the returned ``state`` back to ``/auth/spotify/complete`` so
    we can verify the callback hasn't been forged.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    state = _issue_state_token()
    authorize_url = spotify_service.build_authorize_url(state=state)
    return {
        "data": {
            "authorize_url": authorize_url,
            "state": state,
        }
    }, 200


@api_v1.route("/auth/spotify/complete", methods=["POST"])
@require_auth
def spotify_complete() -> tuple[dict[str, Any], int]:
    """Finalize the Spotify OAuth flow and link the connection to the caller.

    Request body: ``{"code": "...", "state": "..."}``.

    Verifies the state token, exchanges ``code`` with Spotify, upserts
    the caller's MusicServiceConnection row, and returns the refreshed
    user profile. The Spotify account must not already be linked to a
    different Greenroom user.

    Returns:
        Tuple of JSON body (``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing/malformed.
        AppError: ``SPOTIFY_AUTH_FAILED`` if Spotify rejects the code,
            returns an unusable profile, or the profile is already
            linked to a different Greenroom account.
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

    tokens = spotify_service.exchange_code(code)
    profile = spotify_service.get_profile(tokens.access_token)

    session = get_db()
    user = get_current_user()
    _link_spotify_connection(session, user, profile, tokens)

    # Populate spotify_top_* fields inline so the first /for-you render
    # isn't empty. A Spotify outage here should never block the connect
    # flow, so any failure is logged and swallowed — the nightly Celery
    # task (and any subsequent reconnect) will retry.
    try:
        spotify_service.sync_top_artists(
            session, user, access_token=tokens.access_token
        )
    except AppError as exc:
        logger.warning("Inline top-artists sync failed for user %s: %s", user.id, exc)

    return {"data": {"user": users_service.serialize_user(user)}}, 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue_state_token() -> str:
    """Mint a short-lived signed state token for the OAuth round trip.

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
    """Validate an OAuth state token, rejecting forgeries and replays.

    Args:
        state: State value Spotify echoed back on the redirect.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if the state is expired,
            tampered with, or was not minted for this flow.
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
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify OAuth state is invalid or expired.",
            status_code=400,
        ) from exc
    if claims.get("purpose") != _STATE_PURPOSE:
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify OAuth state has the wrong purpose.",
            status_code=400,
        )


def _link_spotify_connection(
    session: Session,
    user: User,
    profile: spotify_service.SpotifyProfile,
    tokens: spotify_service.SpotifyTokens,
) -> None:
    """Create or refresh the caller's Spotify MusicServiceConnection.

    If a connection for this Spotify profile already exists on a
    *different* user, reject the link rather than silently rebinding
    it — that would let attackers steal a Spotify-authenticated account
    by re-consenting through a Knuckles account they control.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated caller.
        profile: Profile fetched from Spotify.
        tokens: OAuth tokens just issued.

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` (409) if the Spotify profile
            is already linked to a different Greenroom user.
    """
    connection = users_repo.get_music_connection(
        session,
        provider=OAuthProvider.SPOTIFY,
        provider_user_id=profile.id,
    )
    if connection is not None and connection.user_id != user.id:
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="Spotify account is already linked to another user.",
            status_code=409,
        )

    if connection is not None:
        users_repo.update_music_connection_tokens(
            session,
            connection,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
        )
    else:
        users_repo.create_music_connection(
            session,
            user_id=user.id,
            provider=OAuthProvider.SPOTIFY,
            provider_user_id=profile.id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
            scopes=tokens.scope,
        )

    users_repo.update_user(
        session,
        user,
        display_name=profile.display_name or user.display_name,
        avatar_url=profile.avatar_url or user.avatar_url,
    )
    users_repo.update_last_login(session, user)
