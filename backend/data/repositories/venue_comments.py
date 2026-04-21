"""Repository functions for venue comments and their votes.

All database access for the ``venue_comments`` and
``venue_comment_votes`` tables goes through this module. The API layer
feeds in already-validated category/body/ip_hash/session_id values;
this module does not enforce spam rules — the service layer does.

The heavy lift here is the two sort modes on
:func:`list_comments_by_venue` and the dedup-aware
:func:`upsert_vote`, which switches its WHERE clause on whether the
voter is logged in (user_id keyed) or a guest (session_id keyed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, case, func, literal, or_, select

from backend.data.models.venue_comments import (
    VenueComment,
    VenueCommentCategory,
    VenueCommentVote,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


# Recency boost window — comments younger than this get a +1 bias added
# to their raw (likes - dislikes) score when sorted by "top", so a
# strong brand-new tip can beat an older comment with a small lead.
RECENCY_BOOST_DAYS = 7


def get_comment_by_id(session: Session, comment_id: uuid.UUID) -> VenueComment | None:
    """Fetch a single comment by its primary key.

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment.

    Returns:
        The :class:`VenueComment` row if found, else None.
    """
    return session.get(VenueComment, comment_id)


def list_comments_by_venue(
    session: Session,
    venue_id: uuid.UUID,
    *,
    category: VenueCommentCategory | None = None,
    sort: str = "top",
    limit: int = 50,
    now: datetime | None = None,
) -> list[tuple[VenueComment, int, int]]:
    """List comments under a venue with their aggregated vote counts.

    Two sort modes are supported:

    * ``"new"`` — newest first, pure reverse-chronological. No vote math.
    * ``"top"`` — (likes - dislikes) + recency boost, descending. A
      boost of +1 is added to any comment newer than
      :data:`RECENCY_BOOST_DAYS`, so a thoughtful tip from this morning
      can outrank a two-week-old comment with one net upvote.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue being browsed.
        category: Optional category filter. None returns all categories.
        sort: Either ``"new"`` or ``"top"``; any other value falls back
            to ``"top"``.
        limit: Maximum number of comments to return. Clamped to 100.
        now: Override for the "current time" anchor used by the recency
            boost. Defaults to :func:`datetime.now` in UTC.

    Returns:
        List of ``(comment, like_count, dislike_count)`` tuples.
    """
    limit = min(max(limit, 1), 100)
    anchor = now or datetime.now(UTC)
    boost_cutoff = anchor - timedelta(days=RECENCY_BOOST_DAYS)

    likes_sq = (
        select(
            VenueCommentVote.comment_id.label("cid"),
            func.coalesce(
                func.sum(case((VenueCommentVote.value == 1, 1), else_=0)), 0
            ).label("likes"),
            func.coalesce(
                func.sum(case((VenueCommentVote.value == -1, 1), else_=0)), 0
            ).label("dislikes"),
        )
        .group_by(VenueCommentVote.comment_id)
        .subquery()
    )

    stmt = (
        select(
            VenueComment,
            func.coalesce(likes_sq.c.likes, 0).label("likes"),
            func.coalesce(likes_sq.c.dislikes, 0).label("dislikes"),
        )
        .outerjoin(likes_sq, likes_sq.c.cid == VenueComment.id)
        .where(VenueComment.venue_id == venue_id)
    )
    if category is not None:
        stmt = stmt.where(VenueComment.category == category.value)

    if sort == "new":
        stmt = stmt.order_by(VenueComment.created_at.desc())
    else:
        net = func.coalesce(likes_sq.c.likes, 0) - func.coalesce(likes_sq.c.dislikes, 0)
        recency_boost = case(
            (VenueComment.created_at >= boost_cutoff, literal(1)),
            else_=literal(0),
        )
        stmt = stmt.order_by(
            (net + recency_boost).desc(),
            VenueComment.created_at.desc(),
        )

    stmt = stmt.limit(limit)
    rows = session.execute(stmt).all()
    return [(row[0], int(row[1]), int(row[2])) for row in rows]


def create_comment(
    session: Session,
    *,
    venue_id: uuid.UUID,
    user_id: uuid.UUID | None,
    category: VenueCommentCategory,
    body: str,
    ip_hash: str | None,
) -> VenueComment:
    """Insert a new comment row.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue being commented on.
        user_id: UUID of the author. May be None so the caller can
            preserve the row after account deletion via SET NULL.
        category: Which category tab the comment belongs to.
        body: Comment body; assumed already length-validated by caller.
        ip_hash: sha256 hex digest of (IP + rotating salt), for rate
            limiting lookups. None when the caller can't compute one.

    Returns:
        The freshly created :class:`VenueComment`, flushed so its id is
        populated.
    """
    comment = VenueComment(
        venue_id=venue_id,
        user_id=user_id,
        category=category,
        body=body,
        ip_hash=ip_hash,
    )
    session.add(comment)
    session.flush()
    return comment


def update_comment_body(
    session: Session, comment: VenueComment, *, body: str
) -> VenueComment:
    """Replace a comment's body text in place.

    Args:
        session: Active SQLAlchemy session.
        comment: The :class:`VenueComment` being edited.
        body: Replacement body. Caller is responsible for validation.

    Returns:
        The updated :class:`VenueComment` (same instance).
    """
    comment.body = body
    session.flush()
    return comment


def delete_comment(session: Session, comment: VenueComment) -> None:
    """Remove a comment row. Cascade drops its votes.

    Args:
        session: Active SQLAlchemy session.
        comment: The :class:`VenueComment` to delete.
    """
    session.delete(comment)
    session.flush()


def upsert_vote(
    session: Session,
    *,
    comment_id: uuid.UUID,
    user_id: uuid.UUID | None,
    session_id: str | None,
    value: int,
) -> VenueCommentVote:
    """Record a +1/-1 vote, dedup-safe per voter.

    Exactly one of ``user_id`` / ``session_id`` must be provided. The
    repository finds any existing vote keyed on that identity and
    updates it in place; otherwise it inserts a new row.

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment being voted on.
        user_id: Voter's user id, when logged in.
        session_id: Voter's browser session id, when signed out.
        value: +1 or -1.

    Returns:
        The :class:`VenueCommentVote` row, flushed.

    Raises:
        ValueError: If both or neither of ``user_id`` / ``session_id``
            is provided, or if ``value`` is not +/- 1.
    """
    if (user_id is None) == (session_id is None):
        raise ValueError("exactly one of user_id or session_id must be set")
    if value not in (1, -1):
        raise ValueError("value must be +1 or -1")

    if user_id is not None:
        stmt = select(VenueCommentVote).where(
            and_(
                VenueCommentVote.comment_id == comment_id,
                VenueCommentVote.user_id == user_id,
            )
        )
    else:
        stmt = select(VenueCommentVote).where(
            and_(
                VenueCommentVote.comment_id == comment_id,
                VenueCommentVote.session_id == session_id,
            )
        )

    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        existing.value = value
        session.flush()
        return existing

    vote = VenueCommentVote(
        comment_id=comment_id,
        user_id=user_id,
        session_id=session_id,
        value=value,
    )
    session.add(vote)
    session.flush()
    return vote


def clear_vote(
    session: Session,
    *,
    comment_id: uuid.UUID,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> bool:
    """Remove the voter's existing vote on a comment, if any.

    Used when a user clicks the same vote arrow twice to "unvote".

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment.
        user_id: Voter's user id, when logged in.
        session_id: Voter's browser session id, when signed out.

    Returns:
        True if a vote was removed, False if there wasn't one to remove.
    """
    if (user_id is None) == (session_id is None):
        raise ValueError("exactly one of user_id or session_id must be set")
    if user_id is not None:
        stmt = select(VenueCommentVote).where(
            and_(
                VenueCommentVote.comment_id == comment_id,
                VenueCommentVote.user_id == user_id,
            )
        )
    else:
        stmt = select(VenueCommentVote).where(
            and_(
                VenueCommentVote.comment_id == comment_id,
                VenueCommentVote.session_id == session_id,
            )
        )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


def count_votes_for_comment(session: Session, comment_id: uuid.UUID) -> tuple[int, int]:
    """Return the (likes, dislikes) total for a single comment.

    Args:
        session: Active SQLAlchemy session.
        comment_id: UUID of the comment.

    Returns:
        Tuple of ``(likes, dislikes)``. Both default to 0 when the
        comment has no votes.
    """
    stmt = select(
        func.coalesce(func.sum(case((VenueCommentVote.value == 1, 1), else_=0)), 0),
        func.coalesce(func.sum(case((VenueCommentVote.value == -1, 1), else_=0)), 0),
    ).where(VenueCommentVote.comment_id == comment_id)
    likes, dislikes = session.execute(stmt).one()
    return int(likes), int(dislikes)


def count_recent_comments_from_ip(
    session: Session, ip_hash: str, *, within: timedelta
) -> int:
    """Count comments from a single ip_hash inside a recency window.

    Used by the service layer's per-IP rate limiter. The query runs
    against ``ix_venue_comments_ip_hash_created_at`` so it stays cheap
    even as the table grows.

    Args:
        session: Active SQLAlchemy session.
        ip_hash: sha256 hex of (IP + salt).
        within: How far back to look, relative to "now".

    Returns:
        The number of matching comments.
    """
    cutoff = datetime.now(UTC) - within
    stmt = select(func.count()).where(
        and_(
            VenueComment.ip_hash == ip_hash,
            VenueComment.created_at >= cutoff,
        )
    )
    return int(session.execute(stmt).scalar_one())


def get_voter_values_for_comments(
    session: Session,
    comment_ids: list[uuid.UUID],
    *,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> dict[uuid.UUID, int]:
    """Look up which of a batch of comments the caller has voted on.

    Used by the API layer to render arrow state on each comment in a
    single pass.

    Args:
        session: Active SQLAlchemy session.
        comment_ids: List of comment ids the caller is about to render.
        user_id: Viewer's user id if logged in.
        session_id: Viewer's guest session id, if any.

    Returns:
        Dict mapping comment_id to +/-1 for each comment the caller has
        voted on. Missing keys mean "no vote".
    """
    if not comment_ids or (user_id is None and session_id is None):
        return {}
    stmt = select(VenueCommentVote.comment_id, VenueCommentVote.value).where(
        VenueCommentVote.comment_id.in_(comment_ids)
    )
    if user_id is not None and session_id is not None:
        stmt = stmt.where(
            or_(
                VenueCommentVote.user_id == user_id,
                VenueCommentVote.session_id == session_id,
            )
        )
    elif user_id is not None:
        stmt = stmt.where(VenueCommentVote.user_id == user_id)
    else:
        stmt = stmt.where(VenueCommentVote.session_id == session_id)
    return {cid: int(val) for cid, val in session.execute(stmt).all()}
