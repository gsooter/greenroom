"""Repository for user-followed artists and venues.

The follow tables are simple many-to-many edges — one row per
``(user, artist)`` or ``(user, venue)`` pair. Idempotent writes use
``ON CONFLICT DO NOTHING`` so repeat follows are safe without a
pre-check round-trip.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.core.text import normalize_artist_name
from backend.data.models.artists import Artist
from backend.data.models.onboarding import FollowedArtist, FollowedVenue
from backend.data.models.venues import Venue

# ---------------------------------------------------------------------------
# Artist follows
# ---------------------------------------------------------------------------


def follow_artist(
    session: Session,
    user_id: uuid.UUID,
    artist_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    """Add a ``(user, artist)`` follow edge, idempotent.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the following user.
        artist_id: UUID of the artist being followed.
        now: Optional override for ``created_at``, for tests.
    """
    stmt = (
        insert(FollowedArtist)
        .values(
            user_id=user_id,
            artist_id=artist_id,
            created_at=now or datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "artist_id"])
    )
    session.execute(stmt)
    session.flush()


def unfollow_artist(session: Session, user_id: uuid.UUID, artist_id: uuid.UUID) -> None:
    """Remove the ``(user, artist)`` follow edge if it exists.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        artist_id: UUID of the artist.
    """
    edge = session.get(FollowedArtist, (user_id, artist_id))
    if edge is not None:
        session.delete(edge)
        session.flush()


def list_followed_artist_ids(session: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Return the set of artist UUIDs a user follows.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        Set of artist UUIDs.
    """
    stmt = select(FollowedArtist.artist_id).where(FollowedArtist.user_id == user_id)
    return set(session.execute(stmt).scalars().all())


def list_followed_artist_signals(
    session: Session,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Build the signal payload the followed-artist scorer needs.

    Joins :class:`FollowedArtist` to :class:`Artist` once so the scorer
    can decide id and name matches against ``Event.spotify_artist_ids``
    and ``Event.artists`` without further lookups.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        Mapping with three keys:

            * ``spotify_ids`` — ``{spotify_id: display_name}`` for every
              followed artist whose row carries a ``spotify_id``.
            * ``names`` — ``{normalized_name: display_name}`` for every
              followed artist (covers events scraped without Spotify
              ids and is the only path for non-Spotify-enriched rows).
            * ``labels`` — ``{artist_id: display_name}`` for the full
              followed set; useful for downstream filter UI.

        Empty dicts are returned when the user follows no artists.
    """
    stmt = (
        select(Artist.id, Artist.name, Artist.normalized_name, Artist.spotify_id)
        .join(FollowedArtist, FollowedArtist.artist_id == Artist.id)
        .where(FollowedArtist.user_id == user_id)
    )
    spotify_ids: dict[str, str] = {}
    names: dict[str, str] = {}
    labels: dict[uuid.UUID, str] = {}
    for artist_id, name, normalized, spotify_id in session.execute(stmt).all():
        if not name:
            continue
        labels[artist_id] = name
        if spotify_id:
            spotify_ids.setdefault(spotify_id, name)
        # Prefer the normalized_name column when present, but fall back
        # to renormalizing on the fly so legacy rows without it still
        # match. ``normalize_artist_name`` is idempotent.
        key = normalized or normalize_artist_name(name)
        if key:
            names.setdefault(key, name)
    return {"spotify_ids": spotify_ids, "names": names, "labels": labels}


def list_followed_venue_labels(
    session: Session,
    user_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    """Return ``{venue_id: display_name}`` for every venue the user follows.

    The followed-venue scorer reads this map directly. The venues
    repository already exposes a paginated list helper; this is the
    cheaper variant when the caller only needs the lookup table.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        Mapping of ``venue_id`` → display name. Empty when the user
        follows no venues.
    """
    stmt = (
        select(Venue.id, Venue.name)
        .join(FollowedVenue, FollowedVenue.venue_id == Venue.id)
        .where(FollowedVenue.user_id == user_id)
    )
    result: dict[uuid.UUID, str] = {}
    for venue_id, venue_name in session.execute(stmt).all():
        if not venue_name:
            continue
        result[venue_id] = venue_name
    return result


def list_followed_artists(
    session: Session,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Artist], int]:
    """Fetch the artists a user follows, newest-first, paginated.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        page: 1-indexed page number.
        per_page: Page size cap.

    Returns:
        Tuple of (artist rows, total count).
    """
    base = (
        select(Artist, FollowedArtist.created_at)
        .join(FollowedArtist, FollowedArtist.artist_id == Artist.id)
        .where(FollowedArtist.user_id == user_id)
    )

    count_stmt = select(FollowedArtist.artist_id).where(
        FollowedArtist.user_id == user_id
    )
    total = len(session.execute(count_stmt).scalars().all())

    stmt = (
        base.order_by(FollowedArtist.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    artists = [row[0] for row in session.execute(stmt).all()]
    return artists, total


# ---------------------------------------------------------------------------
# Venue follows
# ---------------------------------------------------------------------------


def follow_venue(
    session: Session,
    user_id: uuid.UUID,
    venue_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    """Add a ``(user, venue)`` follow edge, idempotent.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the following user.
        venue_id: UUID of the venue being followed.
        now: Optional override for ``created_at``, for tests.
    """
    stmt = (
        insert(FollowedVenue)
        .values(
            user_id=user_id,
            venue_id=venue_id,
            created_at=now or datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "venue_id"])
    )
    session.execute(stmt)
    session.flush()


def follow_venues_bulk(
    session: Session,
    user_id: uuid.UUID,
    venue_ids: list[uuid.UUID],
    *,
    now: datetime | None = None,
) -> int:
    """Add many ``(user, venue)`` edges in a single round-trip.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the following user.
        venue_ids: Venues to follow. Duplicates are deduped server-side.
        now: Optional override for ``created_at``.

    Returns:
        Number of edges written (0 if every supplied venue was already
        followed).
    """
    if not venue_ids:
        return 0
    timestamp = now or datetime.now(UTC)
    rows = [
        {"user_id": user_id, "venue_id": vid, "created_at": timestamp}
        for vid in set(venue_ids)
    ]
    stmt = (
        insert(FollowedVenue)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["user_id", "venue_id"])
    )
    result = session.execute(stmt)
    session.flush()
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount


def unfollow_venue(session: Session, user_id: uuid.UUID, venue_id: uuid.UUID) -> None:
    """Remove the ``(user, venue)`` follow edge if it exists.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        venue_id: UUID of the venue.
    """
    edge = session.get(FollowedVenue, (user_id, venue_id))
    if edge is not None:
        session.delete(edge)
        session.flush()


def list_followed_venue_ids(session: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Return the set of venue UUIDs a user follows.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        Set of venue UUIDs.
    """
    stmt = select(FollowedVenue.venue_id).where(FollowedVenue.user_id == user_id)
    return set(session.execute(stmt).scalars().all())


def list_followed_venues(
    session: Session,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Venue], int]:
    """Fetch the venues a user follows, newest-first, paginated.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        page: 1-indexed page number.
        per_page: Page size cap.

    Returns:
        Tuple of (venue rows, total count).
    """
    base = (
        select(Venue, FollowedVenue.created_at)
        .join(FollowedVenue, FollowedVenue.venue_id == Venue.id)
        .where(FollowedVenue.user_id == user_id)
    )

    count_stmt = select(FollowedVenue.venue_id).where(FollowedVenue.user_id == user_id)
    total = len(session.execute(count_stmt).scalars().all())

    stmt = (
        base.order_by(FollowedVenue.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    venues = [row[0] for row in session.execute(stmt).all()]
    return venues, total
