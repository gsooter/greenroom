"""Recommendation business logic.

Thin layer between the ``/me/recommendations`` API route and the
recommendation engine. Owns:

* When to lazily regenerate a user's list (first read after login, or
  after an explicit refresh) versus when to serve what's already
  persisted.
* How a :class:`Recommendation` row is shaped for the frontend.

Route handlers never call the engine or the repository directly — they
go through this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.data.repositories import users as users_repo
from backend.recommendations import engine as rec_engine
from backend.services import events as events_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.recommendations import Recommendation
    from backend.data.models.users import User


def list_recommendations_for_user(
    session: Session,
    user: User,
    *,
    page: int = 1,
    per_page: int = 20,
    lazy_generate: bool = True,
) -> tuple[list[Recommendation], int]:
    """Return the user's recommendation page, generating on first read.

    If the user has any scoreable signal (cached music-service artists
    or onboarding genre picks) but no recommendations yet — typical
    right after login or right after completing the taste step — we
    regenerate before reading so the For-You page isn't empty on its
    first paint. Subsequent reads hit the already-persisted list.
    Callers that want to force a refresh should call
    :func:`refresh_recommendations_for_user` instead.

    Mirror this gate with the inner gate in
    :func:`backend.recommendations.engine.generate_for_user`: if one
    lifts, the other must too, otherwise the lazy path pays the cost
    of running the engine only to have it short-circuit to zero rows.

    Args:
        session: Active SQLAlchemy session.
        user: The caller.
        page: 1-indexed page number.
        per_page: Rows per page.
        lazy_generate: When True, regenerate if the persisted list is
            empty. Turn off in tests that want to assert "no regen
            happened on this read."

    Returns:
        Tuple of (page of recommendations, total count).
    """
    recs, total = users_repo.list_recommendations(
        session, user.id, page=page, per_page=per_page
    )
    if total == 0 and lazy_generate and _has_scoreable_signal(session, user):
        rec_engine.generate_for_user(session, user)
        session.commit()
        recs, total = users_repo.list_recommendations(
            session, user.id, page=page, per_page=per_page
        )
    return recs, total


def _has_scoreable_signal(session: Session, user: User) -> bool:
    """Cheap probe: does this user have any input the engine can score on?

    The For-You page should not pay the cost of a full scoring pass when
    no scorer would have anything to compare against. This mirrors the
    inner gate in :func:`backend.recommendations.engine.generate_for_user`
    — if one expands, the other must too.

    Args:
        session: Active SQLAlchemy session.
        user: The caller.

    Returns:
        True if at least one of (cached top/recent artists across
        Spotify/Tidal/Apple, onboarding genre picks, saved-event
        venues) is populated.
    """
    if (
        user.spotify_top_artists
        or user.spotify_recent_artists
        or user.tidal_top_artists
        or user.apple_top_artists
        or user.genre_preferences
    ):
        return True
    return bool(users_repo.list_saved_venue_affinity(session, user.id))


def refresh_recommendations_for_user(
    session: Session,
    user: User,
) -> int:
    """Force a full regeneration of the user's recommendation list.

    Commits immediately so a client polling the GET endpoint right
    after POSTing a refresh sees the new rows.

    Args:
        session: Active SQLAlchemy session.
        user: The caller.

    Returns:
        Number of recommendation rows written.
    """
    count = rec_engine.generate_for_user(session, user)
    session.commit()
    return count


def serialize_recommendation(rec: Recommendation) -> dict[str, Any]:
    """Serialize a Recommendation for the ``/me/recommendations`` response.

    Flattens the stored ``score_breakdown`` into a simple
    ``match_reasons`` list so the For-You card only has to read one
    field to render "You listen to X" chips, while still returning the
    raw breakdown for analytics or a debug view.

    Args:
        rec: The recommendation row.

    Returns:
        JSON-safe dict with ``id``, ``score``, ``match_reasons``, and
        an embedded ``event`` summary.
    """
    breakdown = rec.score_breakdown or {}
    match_reasons = breakdown.get("_match_reasons") or []
    return {
        "id": str(rec.id),
        "score": rec.score,
        "generated_at": (rec.generated_at.isoformat() if rec.generated_at else None),
        "is_dismissed": rec.is_dismissed,
        "match_reasons": match_reasons,
        "score_breakdown": {
            key: value for key, value in breakdown.items() if key != "_match_reasons"
        },
        "event": events_service.serialize_event_summary(rec.event),
    }
