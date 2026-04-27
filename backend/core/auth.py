"""Knuckles-backed auth decorator and request-scoped user access.

After the Knuckles cutover (Decision 030), every protected request is
authenticated with a Knuckles-issued RS256 access token. ``require_auth``
verifies tokens locally against the cached Knuckles JWKS and looks up
the corresponding Greenroom :class:`User` row by primary key — Greenroom
``users.id`` and Knuckles ``users.id`` are the same UUID.

If the Knuckles token is valid but no Greenroom row exists yet (first
authenticated request after signup), the decorator provisions the row
lazily from the token's ``sub`` and ``email`` claims. Greenroom never
signs access tokens itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from flask import g, request

from backend.core.database import get_db
from backend.core.exceptions import (
    INVALID_TOKEN,
    AppError,
    UnauthorizedError,
)
from backend.core.knuckles_client import verify_knuckles_token
from backend.data.models.users import User
from backend.data.repositories import users as users_repo


def _extract_bearer_token() -> str:
    """Pull a bearer token out of the ``Authorization`` request header.

    Returns:
        The raw token string.

    Raises:
        UnauthorizedError: If no ``Authorization: Bearer <token>`` header
            is present on the current request.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise UnauthorizedError(message="Missing or malformed Authorization header.")
    token = header[len("bearer ") :].strip()
    if not token:
        raise UnauthorizedError(message="Bearer token is empty.")
    return token


def _provision_user_from_claims(user_id: uuid.UUID, claims: dict[str, Any]) -> User:
    """Create a Greenroom ``users`` row from Knuckles JWT claims.

    Called the first time Greenroom sees an authenticated request for a
    Knuckles user that has never hit the app before. The row is keyed by
    the Knuckles ``sub`` UUID so Greenroom ``users.id`` stays identical
    to Knuckles ``users.id`` for the lifetime of the account.

    Args:
        user_id: The UUID decoded from the token's ``sub`` claim.
        claims: The full Knuckles claim set. ``email`` is required
            because Greenroom uses it for digest sending and display.

    Returns:
        The freshly inserted :class:`User`.

    Raises:
        AppError: ``INVALID_TOKEN`` (401) if the token is missing the
            ``email`` claim Greenroom requires to stand up a profile.
    """
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise AppError(
            code=INVALID_TOKEN,
            message="Access token is missing an email claim.",
            status_code=401,
        )
    session = get_db()
    display_name = claims.get("name")
    return users_repo.create_user(
        session,
        user_id=user_id,
        email=email,
        display_name=display_name if isinstance(display_name, str) else None,
    )


def require_auth[F: Callable[..., Any]](func: F) -> F:
    """Flask route decorator that enforces a valid Knuckles access token.

    Reads the bearer token, verifies it against the cached Knuckles JWKS,
    pulls the user UUID from the ``sub`` claim, loads (or lazily creates)
    the matching Greenroom :class:`User` row, and stashes it on
    ``flask.g.current_user`` so downstream view code can reach it through
    :func:`get_current_user` without re-querying.

    Args:
        func: The Flask view function to wrap.

    Returns:
        The wrapped view function.

    Raises:
        AppError / UnauthorizedError: If the token is missing or
            malformed, signature/audience/issuer validation fails, the
            ``sub`` claim is not a UUID, or the matched Greenroom row
            is deactivated.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """Verify the caller's Knuckles token before delegating to the view.

        Args:
            *args: Positional args forwarded to the wrapped view.
            **kwargs: Keyword args forwarded to the wrapped view.

        Returns:
            Whatever the wrapped view returns.
        """
        token = _extract_bearer_token()
        claims = verify_knuckles_token(token)
        sub = claims.get("sub")
        if not isinstance(sub, str):
            raise AppError(
                code=INVALID_TOKEN,
                message="Access token is missing a subject.",
                status_code=401,
            )
        try:
            user_id = uuid.UUID(sub)
        except ValueError as exc:
            raise AppError(
                code=INVALID_TOKEN,
                message="Access token subject is not a valid UUID.",
                status_code=401,
            ) from exc
        session = get_db()
        user = users_repo.get_user_by_id(session, user_id)
        if user is None:
            user = _provision_user_from_claims(user_id, claims)
        elif not user.is_active:
            raise UnauthorizedError(message="Authenticated user is deactivated.")
        g.current_user = user
        return func(*args, **kwargs)

    return cast("F", wrapper)


def get_current_user() -> User:
    """Return the authenticated user stashed by :func:`require_auth`.

    Returns:
        The current request's :class:`User`.

    Raises:
        UnauthorizedError: If no authenticated user is attached to the
            request (i.e., the decorator was not applied).
    """
    user = getattr(g, "current_user", None)
    if not isinstance(user, User):
        raise UnauthorizedError(message="No authenticated user on request.")
    return user


def try_get_current_user() -> User | None:
    """Best-effort token check for routes that work signed-in or anonymous.

    Public listings (``/events``, ``/venues``) are anonymous-by-default
    but read the authenticated user when present so personalized
    features like ``?sort=for_you`` can opt in without forcing a 401 on
    the public path. Any failure — missing header, invalid signature,
    deactivated account — degrades silently to ``None``.

    Returns:
        The current request's :class:`User` when a valid Knuckles token
        is attached, otherwise ``None``.
    """
    cached = getattr(g, "current_user", None)
    if isinstance(cached, User):
        return cached
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer ") :].strip()
    if not token:
        return None
    try:
        claims = verify_knuckles_token(token)
    except Exception:
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        user_id = uuid.UUID(sub)
    except ValueError:
        return None
    session = get_db()
    user = users_repo.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return None
    g.current_user = user
    return user
