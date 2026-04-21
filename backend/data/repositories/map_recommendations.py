"""Repository functions for community map recommendations and their votes.

All database access for the ``map_recommendations`` and
``map_recommendation_votes`` tables goes through this module. The
service layer (:mod:`backend.services.map_recommendations`, to be
added) enforces eligibility, similarity floors, rate limits, and
auto-suppression; this module only knows how to talk to Postgres.

The heavy lifts here are:

* :func:`list_recommendations_in_bounds` — the feed query that powers
  Tonight's DC Map and Shows Near Me. Filters by bounding box, skips
  suppressed rows, optionally filters by category, and ranks by
  ``(likes - dislikes) + recency boost`` so a fresh tip with no votes
  can still out-rank an older tip with one net like.
* :func:`upsert_vote` — dedup-aware: one row per (recommendation, user)
  for logged-in users and one per (recommendation, session_id) for
  guests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, case, func, literal, or_, select

from backend.data.models.map_recommendations import (
    MapRecommendation,
    MapRecommendationCategory,
    MapRecommendationVote,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


RECENCY_BOOST_DAYS = 7
"""Comments younger than this get a +1 bias added to their raw
(likes - dislikes) score when sorted by "top"."""


def get_recommendation_by_id(
    session: Session, recommendation_id: uuid.UUID
) -> MapRecommendation | None:
    """Fetch a single recommendation by its primary key.

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation.

    Returns:
        The :class:`MapRecommendation` row if found, else None.
    """
    return session.get(MapRecommendation, recommendation_id)


def list_recommendations_in_bounds(
    session: Session,
    *,
    sw_lat: float,
    sw_lng: float,
    ne_lat: float,
    ne_lng: float,
    category: MapRecommendationCategory | None = None,
    sort: str = "top",
    limit: int = 100,
    include_suppressed: bool = False,
    now: datetime | None = None,
) -> list[tuple[MapRecommendation, int, int]]:
    """List recommendations inside a lat/lng bounding box.

    This is the feed query used by Tonight's DC Map and Shows Near Me.
    The bounding box is expected to already represent the viewport the
    caller wants results for; this repo does no geo-widening.

    Two sort modes are supported:

    * ``"new"`` — newest first, pure reverse-chronological.
    * ``"top"`` — (likes - dislikes) + recency boost, descending. A
      boost of +1 is added to any recommendation newer than
      :data:`RECENCY_BOOST_DAYS`.

    Args:
        session: Active SQLAlchemy session.
        sw_lat: Southwest corner latitude (inclusive).
        sw_lng: Southwest corner longitude (inclusive).
        ne_lat: Northeast corner latitude (inclusive).
        ne_lng: Northeast corner longitude (inclusive).
        category: Optional category filter. None returns all categories.
        sort: Either ``"new"`` or ``"top"``; any other value falls back
            to ``"top"``.
        limit: Maximum rows to return. Clamped to 200.
        include_suppressed: When True, suppressed rows are returned
            (used by admin tooling). Public feeds pass False.
        now: Override for the "current time" anchor used by the recency
            boost. Defaults to :func:`datetime.now` in UTC.

    Returns:
        List of ``(recommendation, like_count, dislike_count)`` tuples.
    """
    limit = min(max(limit, 1), 200)
    anchor = now or datetime.now(UTC)
    boost_cutoff = anchor - timedelta(days=RECENCY_BOOST_DAYS)

    votes_sq = (
        select(
            MapRecommendationVote.recommendation_id.label("rid"),
            func.coalesce(
                func.sum(case((MapRecommendationVote.value == 1, 1), else_=0)),
                0,
            ).label("likes"),
            func.coalesce(
                func.sum(case((MapRecommendationVote.value == -1, 1), else_=0)),
                0,
            ).label("dislikes"),
        )
        .group_by(MapRecommendationVote.recommendation_id)
        .subquery()
    )

    stmt = (
        select(
            MapRecommendation,
            func.coalesce(votes_sq.c.likes, 0).label("likes"),
            func.coalesce(votes_sq.c.dislikes, 0).label("dislikes"),
        )
        .outerjoin(votes_sq, votes_sq.c.rid == MapRecommendation.id)
        .where(
            MapRecommendation.latitude >= sw_lat,
            MapRecommendation.latitude <= ne_lat,
            MapRecommendation.longitude >= sw_lng,
            MapRecommendation.longitude <= ne_lng,
        )
    )
    if not include_suppressed:
        stmt = stmt.where(MapRecommendation.suppressed_at.is_(None))
    if category is not None:
        stmt = stmt.where(MapRecommendation.category == category.value)

    if sort == "new":
        stmt = stmt.order_by(MapRecommendation.created_at.desc())
    else:
        net = func.coalesce(votes_sq.c.likes, 0) - func.coalesce(votes_sq.c.dislikes, 0)
        recency_boost = case(
            (MapRecommendation.created_at >= boost_cutoff, literal(1)),
            else_=literal(0),
        )
        stmt = stmt.order_by(
            (net + recency_boost).desc(),
            MapRecommendation.created_at.desc(),
        )

    stmt = stmt.limit(limit)
    rows = session.execute(stmt).all()
    return [(row[0], int(row[1]), int(row[2])) for row in rows]


def create_recommendation(
    session: Session,
    *,
    submitter_user_id: uuid.UUID | None,
    session_id: str | None,
    place_name: str,
    place_address: str | None,
    latitude: float,
    longitude: float,
    similarity_score: float,
    category: MapRecommendationCategory,
    body: str,
    ip_hash: str | None,
) -> MapRecommendation:
    """Insert a new recommendation row.

    Exactly one of ``submitter_user_id`` / ``session_id`` must be set —
    the schema enforces this via a CHECK. This repo raises
    :class:`ValueError` before even hitting the database so the caller
    gets a clean error path.

    Args:
        session: Active SQLAlchemy session.
        submitter_user_id: UUID of the author. None for guest submits.
        session_id: Opaque browser session id for guests. None when a
            user_id is supplied.
        place_name: Apple's canonical name for the verified place.
        place_address: Apple's formatted address, when available.
        latitude: Verified WGS-84 latitude.
        longitude: Verified WGS-84 longitude.
        similarity_score: Verifier confidence, ``[0.80, 1.0]``. The
            service layer is responsible for enforcing the floor.
        category: Which filter chip the recommendation belongs to.
        body: Recommendation body; assumed length-validated by caller.
        ip_hash: sha256 hex digest of (IP + rotating salt), for rate
            limiting lookups. None when the caller can't compute one.

    Returns:
        The freshly created :class:`MapRecommendation`, flushed so its
        id is populated.

    Raises:
        ValueError: If neither or both of ``submitter_user_id`` /
            ``session_id`` is provided.
    """
    if (submitter_user_id is None) and (session_id is None):
        raise ValueError("at least one of submitter_user_id or session_id must be set")
    recommendation = MapRecommendation(
        submitter_user_id=submitter_user_id,
        session_id=session_id,
        place_name=place_name,
        place_address=place_address,
        latitude=latitude,
        longitude=longitude,
        similarity_score=similarity_score,
        category=category,
        body=body,
        ip_hash=ip_hash,
    )
    session.add(recommendation)
    session.flush()
    return recommendation


def update_recommendation_body(
    session: Session, recommendation: MapRecommendation, *, body: str
) -> MapRecommendation:
    """Replace a recommendation's body text in place.

    Args:
        session: Active SQLAlchemy session.
        recommendation: The :class:`MapRecommendation` being edited.
        body: Replacement body. Caller is responsible for validation.

    Returns:
        The updated :class:`MapRecommendation` (same instance).
    """
    recommendation.body = body
    session.flush()
    return recommendation


def delete_recommendation(session: Session, recommendation: MapRecommendation) -> None:
    """Hard-delete a recommendation row. Cascade drops its votes.

    Use this only when a user deletes their own submission. For
    moderation or auto-suppression, prefer
    :func:`suppress_recommendation` — it hides the row without
    destroying the data.

    Args:
        session: Active SQLAlchemy session.
        recommendation: The :class:`MapRecommendation` to delete.
    """
    session.delete(recommendation)
    session.flush()


def suppress_recommendation(
    session: Session,
    recommendation: MapRecommendation,
    *,
    at: datetime | None = None,
) -> MapRecommendation:
    """Mark a recommendation as hidden from public feeds.

    Public feed queries filter on ``suppressed_at IS NULL``, so setting
    this timestamp is the non-destructive way to take a row off the
    map. Admin tooling can restore it with :func:`unsuppress_recommendation`.

    Args:
        session: Active SQLAlchemy session.
        recommendation: The row to suppress.
        at: Override for the suppression timestamp. Defaults to now in
            UTC.

    Returns:
        The updated :class:`MapRecommendation`.
    """
    recommendation.suppressed_at = at or datetime.now(UTC)
    session.flush()
    return recommendation


def unsuppress_recommendation(
    session: Session, recommendation: MapRecommendation
) -> MapRecommendation:
    """Restore a previously suppressed recommendation to public feeds.

    Args:
        session: Active SQLAlchemy session.
        recommendation: The row to un-suppress.

    Returns:
        The updated :class:`MapRecommendation`.
    """
    recommendation.suppressed_at = None
    session.flush()
    return recommendation


def upsert_vote(
    session: Session,
    *,
    recommendation_id: uuid.UUID,
    user_id: uuid.UUID | None,
    session_id: str | None,
    value: int,
) -> MapRecommendationVote:
    """Record a +1/-1 vote, dedup-safe per voter.

    Exactly one of ``user_id`` / ``session_id`` must be provided. The
    repository finds any existing vote keyed on that identity and
    updates it in place; otherwise it inserts a new row.

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation being voted on.
        user_id: Voter's user id, when logged in.
        session_id: Voter's browser session id, when signed out.
        value: +1 or -1.

    Returns:
        The :class:`MapRecommendationVote` row, flushed.

    Raises:
        ValueError: If both or neither of ``user_id`` / ``session_id``
            is provided, or if ``value`` is not +/- 1.
    """
    if (user_id is None) == (session_id is None):
        raise ValueError("exactly one of user_id or session_id must be set")
    if value not in (1, -1):
        raise ValueError("value must be +1 or -1")

    if user_id is not None:
        stmt = select(MapRecommendationVote).where(
            and_(
                MapRecommendationVote.recommendation_id == recommendation_id,
                MapRecommendationVote.user_id == user_id,
            )
        )
    else:
        stmt = select(MapRecommendationVote).where(
            and_(
                MapRecommendationVote.recommendation_id == recommendation_id,
                MapRecommendationVote.session_id == session_id,
            )
        )

    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        existing.value = value
        session.flush()
        return existing

    vote = MapRecommendationVote(
        recommendation_id=recommendation_id,
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
    recommendation_id: uuid.UUID,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> bool:
    """Remove the voter's existing vote on a recommendation, if any.

    Used when a user clicks the same vote arrow twice to "unvote".

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation.
        user_id: Voter's user id, when logged in.
        session_id: Voter's browser session id, when signed out.

    Returns:
        True if a vote was removed, False if there wasn't one to remove.

    Raises:
        ValueError: If neither or both of ``user_id`` / ``session_id``
            is provided.
    """
    if (user_id is None) == (session_id is None):
        raise ValueError("exactly one of user_id or session_id must be set")
    if user_id is not None:
        stmt = select(MapRecommendationVote).where(
            and_(
                MapRecommendationVote.recommendation_id == recommendation_id,
                MapRecommendationVote.user_id == user_id,
            )
        )
    else:
        stmt = select(MapRecommendationVote).where(
            and_(
                MapRecommendationVote.recommendation_id == recommendation_id,
                MapRecommendationVote.session_id == session_id,
            )
        )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


def count_votes_for_recommendation(
    session: Session, recommendation_id: uuid.UUID
) -> tuple[int, int]:
    """Return the (likes, dislikes) total for a single recommendation.

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation.

    Returns:
        Tuple of ``(likes, dislikes)``. Both default to 0 when the
        recommendation has no votes.
    """
    stmt = select(
        func.coalesce(
            func.sum(case((MapRecommendationVote.value == 1, 1), else_=0)), 0
        ),
        func.coalesce(
            func.sum(case((MapRecommendationVote.value == -1, 1), else_=0)), 0
        ),
    ).where(MapRecommendationVote.recommendation_id == recommendation_id)
    likes, dislikes = session.execute(stmt).one()
    return int(likes), int(dislikes)


def count_recent_recommendations_from_ip(
    session: Session, ip_hash: str, *, within: timedelta
) -> int:
    """Count recommendations from a single ip_hash inside a recency window.

    Used by the service layer's per-IP rate limiter. The query runs
    against ``ix_map_recommendations_ip_hash_created_at`` so it stays
    cheap as the table grows.

    Args:
        session: Active SQLAlchemy session.
        ip_hash: sha256 hex of (IP + salt).
        within: How far back to look, relative to "now".

    Returns:
        The number of matching recommendations.
    """
    cutoff = datetime.now(UTC) - within
    stmt = select(func.count()).where(
        and_(
            MapRecommendation.ip_hash == ip_hash,
            MapRecommendation.created_at >= cutoff,
        )
    )
    return int(session.execute(stmt).scalar_one())


def get_voter_values_for_recommendations(
    session: Session,
    recommendation_ids: list[uuid.UUID],
    *,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> dict[uuid.UUID, int]:
    """Look up which of a batch of recommendations the caller has voted on.

    Used by the API layer to render vote-arrow state on each
    recommendation in a single pass.

    Args:
        session: Active SQLAlchemy session.
        recommendation_ids: List of recommendation ids to check.
        user_id: Viewer's user id if logged in.
        session_id: Viewer's guest session id, if any.

    Returns:
        Dict mapping recommendation_id to +/-1 for each recommendation
        the caller has voted on. Missing keys mean "no vote".
    """
    if not recommendation_ids or (user_id is None and session_id is None):
        return {}
    stmt = select(
        MapRecommendationVote.recommendation_id, MapRecommendationVote.value
    ).where(MapRecommendationVote.recommendation_id.in_(recommendation_ids))
    if user_id is not None and session_id is not None:
        stmt = stmt.where(
            or_(
                MapRecommendationVote.user_id == user_id,
                MapRecommendationVote.session_id == session_id,
            )
        )
    elif user_id is not None:
        stmt = stmt.where(MapRecommendationVote.user_id == user_id)
    else:
        stmt = stmt.where(MapRecommendationVote.session_id == session_id)
    return {rid: int(val) for rid, val in session.execute(stmt).all()}
