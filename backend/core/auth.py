"""JWT creation, validation, and route decorators.

After the Knuckles cutover (Decision 030), every protected request is
authenticated with a Knuckles-issued RS256 access token. ``require_auth``
verifies tokens locally against the cached Knuckles JWKS and looks up
the corresponding Greenroom :class:`User` row by primary key — Greenroom
``users.id`` and Knuckles ``users.id`` are the same UUID after the
identity-rewrite migration.

The legacy HS256 helpers (:func:`issue_token`, :func:`verify_token`)
remain only because the soon-to-be-deleted local sign-in services
(magic-link, Google, Apple, passkey under ``backend/services/auth.py``)
still emit those tokens. Those services and helpers are scheduled for
removal as the next step of the Knuckles cutover.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, cast

import jwt
from flask import g, request

from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
    UnauthorizedError,
)
from backend.core.knuckles_client import verify_knuckles_token
from backend.data.models.users import User
from backend.data.repositories import users as users_repo

_JWT_ALGORITHM = "HS256"


def issue_token(user_id: uuid.UUID) -> str:
    """Issue a signed JWT for a newly authenticated user.

    Args:
        user_id: UUID of the user the token represents.

    Returns:
        The encoded JWT string.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_expiry_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> uuid.UUID:
    """Decode and validate a JWT, returning the user UUID.

    Args:
        token: Encoded JWT string.

    Returns:
        The user UUID embedded in the ``sub`` claim.

    Raises:
        AppError: ``TOKEN_EXPIRED`` when expired, ``INVALID_TOKEN`` for
            malformed, tampered, or wrong-shape tokens.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[_JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            code=TOKEN_EXPIRED,
            message="Authentication token has expired.",
            status_code=401,
        ) from exc
    except jwt.PyJWTError as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Authentication token is invalid.",
            status_code=401,
        ) from exc

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise AppError(
            code=INVALID_TOKEN,
            message="Authentication token is missing a subject.",
            status_code=401,
        )
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise AppError(
            code=INVALID_TOKEN,
            message="Authentication token subject is not a valid UUID.",
            status_code=401,
        ) from exc


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


def require_auth[F: Callable[..., Any]](func: F) -> F:
    """Flask route decorator that enforces a valid Knuckles access token.

    Reads the bearer token, verifies it against the cached Knuckles JWKS,
    pulls the user UUID from the ``sub`` claim, loads the matching
    Greenroom :class:`User` row, and stashes it on
    ``flask.g.current_user`` so downstream view code can reach it through
    :func:`get_current_user` without re-querying.

    Args:
        func: The Flask view function to wrap.

    Returns:
        The wrapped view function.

    Raises:
        AppError / UnauthorizedError: If the token is missing or
            malformed, signature/audience/issuer validation fails, the
            ``sub`` claim is not a UUID, or the user row is missing or
            deactivated.
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
        if user is None or not user.is_active:
            raise UnauthorizedError(message="Authenticated user no longer exists.")
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
