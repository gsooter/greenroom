"""Venue comments business logic — validate, submit, list, vote.

The API layer calls these functions and never touches
:mod:`backend.data.repositories.venue_comments` directly. This module
owns:

* Input normalization — trimming, length caps, category parsing.
* Spam gating — honeypot detection and a minimum-account-age check.
  Per-IP rate limiting is applied by the API layer's
  :func:`backend.core.rate_limit.rate_limit` decorator; the
  :func:`hash_request_ip` helper gives both layers a consistent salted
  digest so logs and DB rows line up.
* Ranking hand-off — exposes the two sort modes the repo supports so
  route handlers can pass a validated string straight through.

All serialization converts UUIDs and enums to strings so the API
layer returns JSON-ready dicts without another transform.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.exceptions import (
    COMMENT_NOT_FOUND,
    VENUE_NOT_FOUND,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.data.models.venue_comments import VenueCommentCategory
from backend.data.repositories import venue_comments as comments_repo
from backend.data.repositories import venues as venues_repo

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.users import User
    from backend.data.models.venue_comments import VenueComment

MAX_BODY_LEN = 2000
MIN_BODY_LEN = 2
MIN_ACCOUNT_AGE = timedelta(minutes=2)
VALID_SORTS = frozenset({"new", "top"})


def hash_request_ip(raw_ip: str) -> str:
    """Return a stable sha256 digest of an IP salted with the JWT secret.

    The JWT secret is already a per-environment random value and is
    treated as sensitive, so reusing it as the salt keeps us from
    having to manage a second secret just for comment rate limiting.
    Rotating the JWT secret naturally invalidates every cached hash,
    which is the right behavior.

    Args:
        raw_ip: Caller's IP as returned by
            :func:`backend.core.rate_limit.get_request_ip`.

    Returns:
        Lowercase hex sha256 string (64 chars). Empty input still
        returns a digest — callers should keep the literal ``"unknown"``
        sentinel if they want to avoid bucketing all unknown IPs
        together, but that is their call to make.
    """
    salt = get_settings().jwt_secret_key.encode("utf-8")
    return hashlib.sha256(salt + raw_ip.encode("utf-8")).hexdigest()


def _parse_category(raw: str) -> VenueCommentCategory:
    """Validate and return a :class:`VenueCommentCategory` from a string.

    Args:
        raw: The ``category`` field from the request payload.

    Returns:
        The matching enum value.

    Raises:
        ValidationError: If ``raw`` isn't a recognized category.
    """
    try:
        return VenueCommentCategory(raw)
    except ValueError as exc:
        allowed = ", ".join(c.value for c in VenueCommentCategory)
        raise ValidationError(
            f"Unknown category '{raw}'. Must be one of: {allowed}."
        ) from exc


def _validated_body(raw: str | None) -> str:
    """Trim and length-check a submitted comment body.

    Args:
        raw: The raw body string from the request, or None.

    Returns:
        The trimmed body, guaranteed non-empty and within the length
        cap.

    Raises:
        ValidationError: If the body is missing, too short, or too long.
    """
    if raw is None:
        raise ValidationError("Missing comment body.")
    trimmed = raw.strip()
    if len(trimmed) < MIN_BODY_LEN:
        raise ValidationError("Comment is too short.")
    if len(trimmed) > MAX_BODY_LEN:
        raise ValidationError(f"Comment exceeds the {MAX_BODY_LEN}-character limit.")
    return trimmed


def _assert_sort(raw: str | None) -> str:
    """Coerce a query-param sort value into one of the valid modes.

    Args:
        raw: The ``sort`` query parameter, or None.

    Returns:
        ``"top"`` by default, or ``"new"`` when explicitly requested.
    """
    if raw is None:
        return "top"
    sort = raw.lower()
    return sort if sort in VALID_SORTS else "top"


def _serialize_comment(
    comment: VenueComment,
    likes: int,
    dislikes: int,
    viewer_vote: int | None,
) -> dict[str, Any]:
    """Produce the JSON-ready dict the frontend renders.

    Args:
        comment: The ORM row.
        likes: Aggregated +1 votes.
        dislikes: Aggregated -1 votes.
        viewer_vote: +1, -1, or None depending on whether the current
            viewer has voted on this comment.

    Returns:
        A plain dict ready to hand to ``jsonify``.
    """
    return {
        "id": str(comment.id),
        "venue_id": str(comment.venue_id),
        "user_id": str(comment.user_id) if comment.user_id else None,
        "category": comment.category.value
        if isinstance(comment.category, VenueCommentCategory)
        else comment.category,
        "body": comment.body,
        "likes": likes,
        "dislikes": dislikes,
        "viewer_vote": viewer_vote,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
    }


def list_comments(
    session: Session,
    venue_slug: str,
    *,
    category: str | None = None,
    sort: str | None = None,
    limit: int = 50,
    viewer_user_id: uuid.UUID | None = None,
    viewer_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return serialized comments for a venue under the given filters.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug from the URL path.
        category: Optional category filter string.
        sort: Optional sort mode; defaults to ``"top"``.
        limit: Max rows to return.
        viewer_user_id: The caller's user id if signed in, used to fill
            out ``viewer_vote`` on each serialized comment.
        viewer_session_id: The caller's guest session id, same use.

    Returns:
        A list of serialized comment dicts.

    Raises:
        NotFoundError: If the venue slug doesn't resolve.
    """
    venue = venues_repo.get_venue_by_slug(session, venue_slug)
    if venue is None:
        raise NotFoundError(
            code=VENUE_NOT_FOUND,
            message=f"No venue found with slug {venue_slug!r}.",
        )

    parsed_category = _parse_category(category) if category else None
    rows = comments_repo.list_comments_by_venue(
        session,
        venue.id,
        category=parsed_category,
        sort=_assert_sort(sort),
        limit=limit,
    )
    if not rows:
        return []

    viewer_votes = comments_repo.get_voter_values_for_comments(
        session,
        [c.id for c, _l, _d in rows],
        user_id=viewer_user_id,
        session_id=viewer_session_id,
    )
    return [
        _serialize_comment(
            comment,
            likes=likes,
            dislikes=dislikes,
            viewer_vote=viewer_votes.get(comment.id),
        )
        for comment, likes, dislikes in rows
    ]


def submit_comment(
    session: Session,
    *,
    venue_slug: str,
    user: User,
    category: str,
    body: str,
    honeypot: str | None,
    ip_hash: str | None,
) -> dict[str, Any]:
    """Validate spam gates and insert a new comment.

    Spam gates applied here:

    * Honeypot — if the hidden form field has any value at all, we drop
      the request silently-ish (``ValidationError`` with a bland
      message). Real users can't see the field.
    * Minimum account age — accounts younger than
      :data:`MIN_ACCOUNT_AGE` cannot post. Prevents signup-then-spam
      scripts without punishing real users, who tend to browse for a
      while before their first post anyway.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug from the URL path.
        user: The authenticated author.
        category: Raw category string from the request.
        body: Raw comment body from the request.
        honeypot: Value of the hidden honeypot field; must be blank.
        ip_hash: Already-salted IP hash from the caller.

    Returns:
        The serialized comment dict.

    Raises:
        NotFoundError: If the venue slug doesn't resolve.
        UnauthorizedError: If ``user`` is None.
        ValidationError: For any spam gate, honeypot, or input failure.
    """
    if user is None:
        raise UnauthorizedError("You must sign in to comment.")
    if honeypot:
        # Blanket message; no hint that the honeypot is why we rejected.
        raise ValidationError("Could not post comment.")
    if user.created_at is None or (
        datetime.now(UTC) - _as_aware_utc(user.created_at) < MIN_ACCOUNT_AGE
    ):
        raise ValidationError("Account is too new to post. Try again in a minute.")

    venue = venues_repo.get_venue_by_slug(session, venue_slug)
    if venue is None:
        raise NotFoundError(
            code=VENUE_NOT_FOUND,
            message=f"No venue found with slug {venue_slug!r}.",
        )

    parsed_category = _parse_category(category)
    trimmed = _validated_body(body)

    comment = comments_repo.create_comment(
        session,
        venue_id=venue.id,
        user_id=user.id,
        category=parsed_category,
        body=trimmed,
        ip_hash=ip_hash,
    )
    return _serialize_comment(comment, likes=0, dislikes=0, viewer_vote=None)


def delete_comment(
    session: Session,
    *,
    comment_id: uuid.UUID,
    user: User,
) -> None:
    """Allow the author of a comment to delete it.

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment being deleted.
        user: The authenticated caller.

    Raises:
        NotFoundError: If no comment exists with that id.
        UnauthorizedError: If the caller is not signed in.
        ForbiddenError: If the caller is not the comment's author.
    """
    if user is None:
        raise UnauthorizedError("You must sign in to delete comments.")
    comment = comments_repo.get_comment_by_id(session, comment_id)
    if comment is None:
        raise NotFoundError(
            code=COMMENT_NOT_FOUND,
            message=f"No comment found with id {comment_id}.",
        )
    if comment.user_id != user.id:
        raise ForbiddenError("You can only delete your own comments.")
    comments_repo.delete_comment(session, comment)


def cast_vote(
    session: Session,
    *,
    comment_id: uuid.UUID,
    value: int,
    user: User | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Record or update a vote on a comment.

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment being voted on.
        value: +1, -1, or 0. ``0`` clears the voter's prior vote.
        user: The authenticated voter, or None for a guest.
        session_id: Guest session id when ``user`` is None.

    Returns:
        ``{"likes": int, "dislikes": int, "viewer_vote": int | None}``.

    Raises:
        NotFoundError: If the comment doesn't exist.
        UnauthorizedError: If both identities are missing.
        ValidationError: If ``value`` is not -1, 0, or +1.
    """
    if user is None and not session_id:
        raise UnauthorizedError("You must sign in or have a session to vote.")
    if value not in (-1, 0, 1):
        raise ValidationError("Vote value must be -1, 0, or +1.")

    comment = comments_repo.get_comment_by_id(session, comment_id)
    if comment is None:
        raise NotFoundError(
            code=COMMENT_NOT_FOUND,
            message=f"No comment found with id {comment_id}.",
        )

    user_id = user.id if user is not None else None
    effective_session_id = None if user is not None else session_id
    if value == 0:
        comments_repo.clear_vote(
            session,
            comment_id=comment_id,
            user_id=user_id,
            session_id=effective_session_id,
        )
        viewer_vote: int | None = None
    else:
        comments_repo.upsert_vote(
            session,
            comment_id=comment_id,
            user_id=user_id,
            session_id=effective_session_id,
            value=value,
        )
        viewer_vote = value

    likes, dislikes = comments_repo.count_votes_for_comment(session, comment_id)
    return {"likes": likes, "dislikes": dislikes, "viewer_vote": viewer_vote}


def _as_aware_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to aware UTC.

    The users table mixes in TimestampMixin whose ``created_at`` is
    stored as a naive timestamp; rather than change the schema, we
    normalize on read so age comparisons use a single timezone.

    Args:
        dt: The datetime to coerce.

    Returns:
        A timezone-aware datetime in UTC.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


__all__ = [
    "MAX_BODY_LEN",
    "MIN_ACCOUNT_AGE",
    "MIN_BODY_LEN",
    "cast_vote",
    "delete_comment",
    "hash_request_ip",
    "list_comments",
    "submit_comment",
]
