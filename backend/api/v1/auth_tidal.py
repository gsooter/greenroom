"""Tidal OAuth route handlers.

Tidal is a music-service *connect* (parallel to Spotify — see
``auth.py``): the caller is already authenticated via Knuckles and now
links their Tidal account to their Greenroom profile. Greenroom does
not issue any identity tokens from this flow; the caller keeps using
their existing Knuckles access token.

Two endpoints, both guarded by :func:`require_auth`:

1. ``GET /api/v1/auth/tidal/start`` — returns the Tidal consent URL
   and a signed ``state`` token. The client performs a full-page
   navigation to that URL.

2. ``POST /api/v1/auth/tidal/complete`` — called by the frontend
   callback page with the ``code`` and ``state`` Tidal appended to
   the redirect. Exchanges the code for tokens and upserts the
   ``MusicServiceConnection`` row attached to the caller's user.

State is carried as a short-lived JWT (10-minute expiry,
``purpose = tidal_oauth_state``). Same stateless pattern as the
Spotify flow in ``auth.py``.
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
    TIDAL_AUTH_FAILED,
    AppError,
    ValidationError,
)
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider, User
from backend.data.repositories import users as users_repo
from backend.services import tidal as tidal_service
from backend.services import users as users_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

_STATE_PURPOSE = "tidal_oauth_state"
_STATE_TTL_SECONDS = 10 * 60
_STATE_ALGORITHM = "HS256"


@api_v1.route("/auth/tidal/start", methods=["GET"])
@require_auth
def tidal_start() -> tuple[dict[str, Any], int]:
    """Return the Tidal consent URL and a signed state token.

    The PKCE code verifier is carried inside the state JWT — the browser
    never sees it — so the `/complete` handler can reuse it for the
    token exchange without needing a server-side session store.

    Returns:
        Tuple of JSON body (``authorize_url``, ``state``) and HTTP 200.
    """
    verifier, challenge = tidal_service.generate_pkce_pair()
    state = _issue_state_token(verifier=verifier)
    authorize_url = tidal_service.build_authorize_url(
        state=state, code_challenge=challenge
    )
    return {
        "data": {
            "authorize_url": authorize_url,
            "state": state,
        }
    }, 200


@api_v1.route("/auth/tidal/complete", methods=["POST"])
@require_auth
def tidal_complete() -> tuple[dict[str, Any], int]:
    """Finalize the Tidal OAuth flow and link the connection to the caller.

    Request body: ``{"code": "...", "state": "..."}``.

    Verifies the state token, exchanges ``code`` with Tidal, upserts
    the caller's MusicServiceConnection row, and returns the refreshed
    user profile. The Tidal account must not already be linked to a
    different Greenroom user.

    Returns:
        Tuple of JSON body (``user``) and HTTP 200.

    Raises:
        ValidationError: If ``code`` or ``state`` is missing/malformed.
        AppError: ``TIDAL_AUTH_FAILED`` if Tidal rejects the code,
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

    code_verifier = _verify_state_token(state)

    tokens = tidal_service.exchange_code(code, code_verifier=code_verifier)
    if not tokens.user_id:
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal token response missing user_id.",
            status_code=502,
        )
    # Profile enrichment is best-effort. Tidal's public v2 ``/users/{id}``
    # endpoint isn't reachable for consumer-scoped tokens (the gateway
    # returns a bodyless 404), so a 502 here would block users from
    # connecting at all. We already have the stable user id from the
    # token response — that's all ``_link_tidal_connection`` actually
    # needs. ``display_name`` / ``avatar_url`` stay None; they're UX
    # polish and the user already has both from Knuckles.
    try:
        profile = tidal_service.get_profile(tokens.access_token, tokens.user_id)
    except AppError as exc:
        logger.info(
            "Tidal profile enrichment skipped for user_id=%s: %s",
            tokens.user_id,
            exc,
        )
        profile = tidal_service.TidalProfile(
            id=tokens.user_id,
            email=None,
            display_name=None,
            avatar_url=None,
        )

    session = get_db()
    user = get_current_user()
    _link_tidal_connection(session, user, profile, tokens)

    # Inline sync so the first /for-you render after connect has data.
    # Tidal outages must never block the connect flow — log and swallow.
    try:
        tidal_service.sync_top_artists(
            session,
            user,
            access_token=tokens.access_token,
            user_id=tokens.user_id,
        )
    except AppError as exc:
        logger.warning(
            "Inline Tidal top-artists sync failed for user %s: %s", user.id, exc
        )

    return {"data": {"user": users_service.serialize_user(user)}}, 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue_state_token(*, verifier: str) -> str:
    """Mint a short-lived signed state token for the Tidal OAuth round trip.

    The PKCE ``code_verifier`` is embedded as a claim so ``/complete``
    can retrieve it without any server-side session state.

    Args:
        verifier: PKCE code verifier to embed in the token.

    Returns:
        Encoded JWT valid for ``_STATE_TTL_SECONDS``.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "purpose": _STATE_PURPOSE,
        "nonce": secrets.token_urlsafe(16),
        "pkce": verifier,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_STATE_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_STATE_ALGORITHM)


def _verify_state_token(state: str) -> str:
    """Validate a Tidal OAuth state token and return the embedded PKCE verifier.

    Args:
        state: State value Tidal echoed back on the redirect.

    Returns:
        The PKCE ``code_verifier`` that was stashed in the token.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` if the state is expired,
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
            code=TIDAL_AUTH_FAILED,
            message="Tidal OAuth state is invalid or expired.",
            status_code=400,
        ) from exc
    if claims.get("purpose") != _STATE_PURPOSE:
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal OAuth state has the wrong purpose.",
            status_code=400,
        )
    verifier = claims.get("pkce")
    if not isinstance(verifier, str) or not verifier:
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal OAuth state is missing the PKCE verifier.",
            status_code=400,
        )
    return verifier


def _link_tidal_connection(
    session: Session,
    user: User,
    profile: tidal_service.TidalProfile,
    tokens: tidal_service.TidalTokens,
) -> None:
    """Create or refresh the caller's Tidal MusicServiceConnection.

    If a connection for this Tidal profile already exists on a
    *different* user, reject the link rather than silently rebinding
    it — same safeguard as the Spotify flow.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated caller.
        profile: Profile fetched from Tidal.
        tokens: OAuth tokens just issued.

    Raises:
        AppError: ``TIDAL_AUTH_FAILED`` (409) if the Tidal profile is
            already linked to a different Greenroom user.
    """
    connection = users_repo.get_music_connection(
        session,
        provider=OAuthProvider.TIDAL,
        provider_user_id=profile.id,
    )
    if connection is not None and connection.user_id != user.id:
        raise AppError(
            code=TIDAL_AUTH_FAILED,
            message="Tidal account is already linked to another user.",
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
            provider=OAuthProvider.TIDAL,
            provider_user_id=profile.id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
            scopes=tokens.scope,
        )

    users_repo.update_user(
        session,
        user,
        display_name=user.display_name or profile.display_name,
        avatar_url=user.avatar_url or profile.avatar_url,
    )
