"""Business logic for artist and venue follows + artist search.

Routes call into this module; they never touch
:mod:`backend.data.repositories.follows` directly. The module covers:

* Artist search (DB-only for v1 — see the TODO in ``NEXT_SPRINT.md``
  for the planned Spotify-API fan-out).
* Per-follow / bulk-follow helpers with consistent validation so the
  onboarding batch-follow and the settings single-follow paths share
  the same "resource must exist" and "clamp at N" semantics.
* Serializers that mirror the summary shape already used by
  :mod:`backend.services.venues` and the artist payload used elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.exceptions import (
    VENUE_NOT_FOUND,
    NotFoundError,
    ValidationError,
)
from backend.data.repositories import artists as artists_repo
from backend.data.repositories import follows as follows_repo
from backend.data.repositories import venues as venues_repo
from backend.services import venues as venues_service

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.artists import Artist
    from backend.data.models.users import User
    from backend.data.models.venues import Venue


ARTIST_NOT_FOUND = "ARTIST_NOT_FOUND"

_MAX_SEARCH_LIMIT = 25
_MAX_BULK_FOLLOW_VENUES = 200


# ---------------------------------------------------------------------------
# Artist search
# ---------------------------------------------------------------------------


def search_artists_for_user(
    session: Session, user: User, *, query: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Search artists by name, tagging each row with the user's follow state.

    v1 uses a DB-only substring match against ``normalized_name``. A
    future v2 fan-out to the Spotify Search API for thin local result
    sets is tracked in NEXT_SPRINT.md.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user (used for the ``is_followed`` flag).
        query: Raw search string from the user.
        limit: Maximum rows to return. Clamped at
            :data:`_MAX_SEARCH_LIMIT` so a client cannot pull the whole
            artist table.

    Returns:
        List of artist summary dicts with ``is_followed`` attached.

    Raises:
        ValidationError: If ``limit`` is not positive.
    """
    if limit <= 0:
        raise ValidationError("limit must be a positive integer.")
    clamped = min(limit, _MAX_SEARCH_LIMIT)

    artists = artists_repo.search_artists(session, query=query, limit=clamped)
    if not artists:
        return []

    followed_ids = follows_repo.list_followed_artist_ids(session, user.id)
    return [
        serialize_artist_summary(a, is_followed=a.id in followed_ids) for a in artists
    ]


# ---------------------------------------------------------------------------
# Artist follows
# ---------------------------------------------------------------------------


def follow_artist(session: Session, user: User, artist_id: uuid.UUID) -> None:
    """Create a ``(user, artist)`` follow edge if missing.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        artist_id: UUID of the artist to follow.

    Raises:
        NotFoundError: If no artist has that ID.
    """
    if artists_repo.get_artist_by_id(session, artist_id) is None:
        raise NotFoundError(
            code=ARTIST_NOT_FOUND,
            message=f"No artist found with id {artist_id}",
        )
    follows_repo.follow_artist(session, user.id, artist_id)


def unfollow_artist(session: Session, user: User, artist_id: uuid.UUID) -> None:
    """Remove a ``(user, artist)`` follow edge.

    Idempotent — a stale UI unfollow click on an already-deleted edge
    is a no-op.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        artist_id: UUID of the artist.
    """
    follows_repo.unfollow_artist(session, user.id, artist_id)


def list_followed_artists(
    session: Session,
    user: User,
    *,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Return the caller's followed artists, newest-first, paginated.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        page: 1-indexed page number.
        per_page: Page size cap (clamped to 100 at the route layer).

    Returns:
        Tuple of (serialized artist summaries, total count).
    """
    artists, total = follows_repo.list_followed_artists(
        session, user.id, page=page, per_page=per_page
    )
    payload = [serialize_artist_summary(a, is_followed=True) for a in artists]
    return payload, total


# ---------------------------------------------------------------------------
# Venue follows
# ---------------------------------------------------------------------------


def follow_venues_bulk(session: Session, user: User, venue_ids: list[uuid.UUID]) -> int:
    """Add many venue follows in one round-trip.

    Used by the Step 2 venue grid on ``/welcome`` where the user
    selects several venues at once. Validates that every venue exists
    before writing anything — either the whole batch lands or none of
    it does.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        venue_ids: Venue UUIDs to follow. Duplicates are deduped.

    Returns:
        Number of new edges written (0 if every supplied venue was
        already followed or the list was empty).

    Raises:
        ValidationError: If the request exceeds
            :data:`_MAX_BULK_FOLLOW_VENUES`.
        NotFoundError: If any supplied venue id does not exist.
    """
    if not venue_ids:
        return 0
    if len(venue_ids) > _MAX_BULK_FOLLOW_VENUES:
        raise ValidationError(
            f"Cannot follow more than {_MAX_BULK_FOLLOW_VENUES} venues at once."
        )
    unique = list(dict.fromkeys(venue_ids))
    # Resolve missing IDs up front so partial follows don't happen.
    for vid in unique:
        if venues_repo.get_venue_by_id(session, vid) is None:
            raise NotFoundError(
                code=VENUE_NOT_FOUND,
                message=f"No venue found with id {vid}",
            )
    return follows_repo.follow_venues_bulk(session, user.id, unique)


def unfollow_venue(session: Session, user: User, venue_id: uuid.UUID) -> None:
    """Remove a ``(user, venue)`` follow edge.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        venue_id: UUID of the venue.
    """
    follows_repo.unfollow_venue(session, user.id, venue_id)


def list_followed_venues(
    session: Session,
    user: User,
    *,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Return the caller's followed venues, newest-first, paginated.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        page: 1-indexed page number.
        per_page: Page size cap.

    Returns:
        Tuple of (serialized venue summaries, total count).
    """
    rows, total = follows_repo.list_followed_venues(
        session, user.id, page=page, per_page=per_page
    )
    return [venues_service.serialize_venue_summary(v) for v in rows], total


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def serialize_artist_summary(artist: Artist, *, is_followed: bool) -> dict[str, Any]:
    """Return the compact artist shape used by search + followed-list.

    Args:
        artist: The :class:`Artist` row.
        is_followed: Whether the authenticated caller already follows
            this artist. The UI uses this to render the follow button
            in its correct state without a second round-trip.

    Returns:
        Dict with ``id``, ``name``, ``genres``, and ``is_followed``.
    """
    return {
        "id": str(artist.id),
        "name": artist.name,
        "genres": artist.genres or [],
        "is_followed": is_followed,
    }


def serialize_venue_summary(venue: Venue, *, is_followed: bool) -> dict[str, Any]:
    """Return the compact venue shape tagged with the caller's follow state.

    Args:
        venue: The :class:`Venue` row.
        is_followed: Whether the authenticated caller already follows
            this venue.

    Returns:
        The standard venue-summary dict plus an ``is_followed`` key.
    """
    payload = venues_service.serialize_venue_summary(venue)
    payload["is_followed"] = is_followed
    return payload
