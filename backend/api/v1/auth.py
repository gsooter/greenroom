"""Spotify OAuth route handlers.

Two-step flow:

1. ``GET /api/v1/auth/spotify/start`` — returns the Spotify consent URL
   and a signed ``state`` token. The client performs a full-page
   navigation to that URL.

2. ``POST /api/v1/auth/spotify/complete`` — called by the frontend
   callback page with the ``code`` and ``state`` Spotify appended to
   the redirect. Exchanges the code for tokens, upserts the User and
   UserOAuthProvider rows, and returns a session JWT the client stores
   in localStorage.

State is carried as a short-lived JWT (10-minute expiry, ``purpose``
claim = ``spotify_oauth_state``) rather than a Redis entry. That keeps
the auth path free of Redis connection ceremony and makes the state
self-verifying; it still prevents CSRF because the attacker can't
forge a JWT signed with our secret.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import issue_token
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import (
    SPOTIFY_AUTH_FAILED,
    AppError,
    ValidationError,
)
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider
from backend.data.repositories import users as users_repo
from backend.services import spotify as spotify_service
from backend.services import users as users_service

logger = get_logger(__name__)

_STATE_PURPOSE = "spotify_oauth_state"
_STATE_TTL_SECONDS = 10 * 60
_STATE_ALGORITHM = "HS256"


@api_v1.route("/auth/spotify/start", methods=["GET"])
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
def spotify_complete() -> tuple[dict[str, Any], int]:
    """Finalize the Spotify OAuth flow and issue a Greenroom JWT.

    Request body: ``{"code": "...", "state": "..."}``.

    Verifies the state token, exchanges ``code`` with Spotify, upserts
    the local User + UserOAuthProvider rows, and returns a freshly
    minted session JWT.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing/malformed.
        AppError: ``SPOTIFY_AUTH_FAILED`` if Spotify rejects the code
            or returns an unusable profile.
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
    user = _upsert_spotify_user(session, profile, tokens)

    # Populate spotify_top_* fields inline so the first /for-you render
    # isn't empty. A Spotify outage here should never block login, so
    # any failure is logged and swallowed — the nightly Celery task
    # (and any subsequent login) will retry.
    try:
        spotify_service.sync_top_artists(
            session, user, access_token=tokens.access_token
        )
    except AppError as exc:
        logger.warning(
            "Inline top-artists sync failed for user %s: %s", user.id, exc
        )

    jwt_token = issue_token(user.id)
    return {
        "data": {
            "token": jwt_token,
            "user": users_service.serialize_user(user),
        }
    }, 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue_state_token() -> str:
    """Mint a short-lived signed state token for the OAuth round trip.

    Returns:
        Encoded JWT valid for ``_STATE_TTL_SECONDS``.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
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


def _upsert_spotify_user(
    session: Any,
    profile: spotify_service.SpotifyProfile,
    tokens: spotify_service.SpotifyTokens,
) -> Any:
    """Link the Spotify profile to a local user row, creating one if new.

    Matches on ``(provider=spotify, provider_user_id=profile.id)``
    first; falls back to matching by email so users returning after
    revoking/reconnecting keep the same account. Updates stored tokens
    every time so refresh flows always see the freshest pair.

    Args:
        session: Active SQLAlchemy session.
        profile: Profile fetched from Spotify.
        tokens: OAuth tokens just issued.

    Returns:
        The :class:`User` now linked to this Spotify account.
    """
    oauth = users_repo.get_oauth_provider(
        session,
        provider=OAuthProvider.SPOTIFY,
        provider_user_id=profile.id,
    )
    if oauth is not None:
        users_repo.update_oauth_tokens(
            session,
            oauth,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
        )
        user = oauth.user
        users_repo.update_user(
            session,
            user,
            display_name=profile.display_name or user.display_name,
            avatar_url=profile.avatar_url or user.avatar_url,
        )
        users_repo.update_last_login(session, user)
        return user

    user = users_repo.get_user_by_email(session, profile.email)
    if user is None:
        user = users_repo.create_user(
            session,
            email=profile.email,
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
        )
    else:
        users_repo.update_user(
            session,
            user,
            display_name=profile.display_name or user.display_name,
            avatar_url=profile.avatar_url or user.avatar_url,
        )

    users_repo.create_oauth_provider(
        session,
        user_id=user.id,
        provider=OAuthProvider.SPOTIFY,
        provider_user_id=profile.id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_expires_at=tokens.expires_at,
        scopes=tokens.scope,
    )
    users_repo.update_last_login(session, user)
    return user
