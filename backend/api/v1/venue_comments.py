"""Venue comments route handlers.

Thin endpoints that validate path and body input and hand off to
:mod:`backend.services.venue_comments`. The module owns three concerns
the service layer cannot see: the Flask request/response bridge, the
per-IP :func:`backend.core.rate_limit.rate_limit` decorator on
``POST /venues/<slug>/comments``, and the "optional auth" dance so
signed-out callers can still load comments and vote as guests.

All routes return the standard ``{"data": ..., "meta": ...}`` / error
envelope defined in ``CLAUDE.md``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flask import g, request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.core.knuckles import verify_knuckles_token
from backend.core.logging import get_logger
from backend.core.rate_limit import get_request_ip, rate_limit
from backend.data.repositories import users as users_repo
from backend.services import venue_comments as service

if TYPE_CHECKING:
    from backend.data.models.users import User

logger = get_logger(__name__)

_MAX_SESSION_ID_LEN = 64
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 100


@api_v1.route("/venues/<slug>/comments", methods=["GET"])
def list_venue_comments(slug: str) -> tuple[dict[str, Any], int]:
    """List comments for a venue with optional category and sort filters.

    Query parameters:
        category: string — one of the :class:`VenueCommentCategory`
            values. Omit for all categories.
        sort: string — ``"top"`` (default) or ``"new"``.
        limit: int — max rows, clamped to ``100``.
        session_id: string — guest session id used to populate
            ``viewer_vote`` when the caller isn't signed in.

    Args:
        slug: Venue slug from the URL path.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If the venue slug doesn't resolve.
        ValidationError: If ``category`` is unrecognized or ``limit``
            is not an int.
    """
    session = get_db()
    viewer = _maybe_current_user()
    viewer_session_id = _sanitized_session_id(request.args.get("session_id"))
    limit = _parse_limit(request.args.get("limit"))

    comments = service.list_comments(
        session,
        slug,
        category=request.args.get("category"),
        sort=request.args.get("sort"),
        limit=limit,
        viewer_user_id=viewer.id if viewer is not None else None,
        viewer_session_id=viewer_session_id if viewer is None else None,
    )
    return {"data": comments, "meta": {"count": len(comments)}}, 200


@api_v1.route("/venues/<slug>/comments", methods=["POST"])
@require_auth
@rate_limit("venue_comment_submit_ip", limit=20, window_seconds=3600)
def submit_venue_comment(slug: str) -> tuple[dict[str, Any], int]:
    """Create a new comment on a venue.

    Request body:
        ``{"category": str, "body": str, "honeypot"?: str}``. The
        ``honeypot`` field must be empty or absent — a non-empty value
        is rejected with a generic ``VALIDATION_ERROR`` so bots get no
        hint that the field is the trap.

    Args:
        slug: Venue slug from the URL path.

    Returns:
        Tuple of JSON response body and HTTP 201 status code.

    Raises:
        UnauthorizedError: If the caller is not signed in.
        NotFoundError: If the venue slug doesn't resolve.
        ValidationError: If the body fails any spam gate or length
            check.
        RateLimitExceededError: If the caller has exceeded the per-IP
            submission cap.
    """
    session = get_db()
    user = get_current_user()
    payload = _require_json_object()

    comment = service.submit_comment(
        session,
        venue_slug=slug,
        user=user,
        category=_require_string(payload, "category"),
        body=_require_string(payload, "body"),
        honeypot=payload.get("honeypot"),
        ip_hash=service.hash_request_ip(get_request_ip()),
    )
    return {"data": comment}, 201


@api_v1.route("/venues/<slug>/comments/<comment_id>/vote", methods=["POST"])
def vote_on_venue_comment(
    slug: str,
    comment_id: str,
) -> tuple[dict[str, Any], int]:
    """Cast, change, or clear a vote on a comment.

    Request body:
        ``{"value": int, "session_id"?: str}``. ``value`` must be
        ``-1``, ``0`` (clear), or ``+1``. ``session_id`` is required
        when the caller is signed out so guest votes can be deduped.

    Args:
        slug: Venue slug from the URL path. Unused by the handler but
            present for URL consistency and so that the comment's
            venue can be inferred client-side.
        comment_id: UUID string of the comment being voted on.

    Returns:
        Tuple of JSON response body and HTTP 200 status code. Body
        shape: ``{"likes": int, "dislikes": int, "viewer_vote": int | None}``.

    Raises:
        UnauthorizedError: If neither auth nor session_id is provided.
        NotFoundError: If the comment doesn't exist.
        ValidationError: If ``value`` or ``comment_id`` is malformed.
    """
    session = get_db()
    parsed_id = _parse_uuid(comment_id, field="comment_id")
    viewer = _maybe_current_user()
    payload = _require_json_object()
    value = _parse_vote_value(payload.get("value"))
    raw_session_id = (
        None if viewer is not None else _sanitized_session_id(payload.get("session_id"))
    )

    result = service.cast_vote(
        session,
        comment_id=parsed_id,
        value=value,
        user=viewer,
        session_id=raw_session_id,
    )
    return {"data": result}, 200


@api_v1.route("/venues/<slug>/comments/<comment_id>", methods=["DELETE"])
@require_auth
def delete_venue_comment(
    slug: str,
    comment_id: str,
) -> tuple[dict[str, Any], int]:
    """Delete one of the caller's own comments.

    Args:
        slug: Venue slug from the URL path. Unused by the handler.
        comment_id: UUID string of the comment being deleted.

    Returns:
        Tuple of empty body and HTTP 204 status code.

    Raises:
        UnauthorizedError: If the caller is not signed in.
        NotFoundError: If the comment doesn't exist.
        ForbiddenError: If the caller is not the comment's author.
        ValidationError: If ``comment_id`` is not a valid UUID.
    """
    session = get_db()
    parsed_id = _parse_uuid(comment_id, field="comment_id")
    service.delete_comment(
        session,
        comment_id=parsed_id,
        user=get_current_user(),
    )
    return {}, 204


def _maybe_current_user() -> User | None:
    """Return the authenticated user if a valid bearer token is present.

    Unlike :func:`backend.core.auth.require_auth`, this helper does
    not raise when authentication is missing or fails — it just
    returns ``None`` so the caller can fall back to guest behavior.

    Returns:
        The :class:`User` on a successful verification, else ``None``.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer ") :].strip()
    if not token:
        return None
    try:
        claims = verify_knuckles_token(token)
        sub = claims.get("sub")
        if not isinstance(sub, str):
            return None
        user_id = uuid.UUID(sub)
    except Exception:
        # Silent fallback to guest — bad tokens on a public endpoint
        # shouldn't break the page.
        logger.debug("venue_comment_optional_auth_rejected", exc_info=True)
        return None

    session = get_db()
    user = users_repo.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return None
    g.current_user = user
    return user


def _require_json_object() -> dict[str, Any]:
    """Pull a JSON object body off the current request.

    Returns:
        The decoded dict.

    Raises:
        ValidationError: If the body is missing or not a JSON object.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    return payload


def _require_string(payload: dict[str, Any], key: str) -> str:
    """Extract a required non-empty string field from a payload.

    Args:
        payload: Request body dict.
        key: Name of the expected field.

    Returns:
        The field value.

    Raises:
        ValidationError: If missing or not a string.
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Missing '{key}' field.")
    return value


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    """Parse a path segment as a UUID, raising a 422 if malformed.

    Args:
        value: The raw string from the URL.
        field: Name of the field, used in the error message.

    Returns:
        The parsed UUID.

    Raises:
        ValidationError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid UUID: '{value}'.") from exc


def _parse_limit(raw: str | None) -> int:
    """Parse and clamp the ``limit`` query parameter.

    Args:
        raw: Raw query-string value, or None.

    Returns:
        An integer between 1 and :data:`_MAX_LIST_LIMIT`. Defaults to
        :data:`_DEFAULT_LIST_LIMIT` when absent.

    Raises:
        ValidationError: If ``raw`` is present but not a positive int.
    """
    if raw is None or raw == "":
        return _DEFAULT_LIST_LIMIT
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValidationError("limit must be an integer.") from exc
    if parsed < 1:
        raise ValidationError("limit must be positive.")
    return min(parsed, _MAX_LIST_LIMIT)


def _parse_vote_value(raw: Any) -> int:
    """Parse the ``value`` field on a vote payload.

    Args:
        raw: The JSON-decoded value. Booleans are rejected outright —
            ``True`` otherwise passes ``isinstance(..., int)``.

    Returns:
        An int in ``{-1, 0, 1}``.

    Raises:
        ValidationError: If missing or not one of the three legal ints.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError("value must be -1, 0, or +1.")
    value: int = raw
    if value not in (-1, 0, 1):
        raise ValidationError("value must be -1, 0, or +1.")
    return value


def _sanitized_session_id(raw: Any) -> str | None:
    """Validate and return a non-empty guest session id string.

    Args:
        raw: The raw value as pulled from the query string or payload.

    Returns:
        A stripped non-empty string of at most
        :data:`_MAX_SESSION_ID_LEN` chars, or ``None`` if missing or
        malformed.
    """
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_SESSION_ID_LEN:
        return None
    return trimmed


__all__ = [
    "delete_venue_comment",
    "list_venue_comments",
    "submit_venue_comment",
    "vote_on_venue_comment",
]
