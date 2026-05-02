"""Repository functions for :class:`backend.data.models.artists.Artist`.

All database access for the ``artists`` table goes through this module.
Scraper ingestion uses :func:`upsert_artist_by_name` to collapse
duplicate spellings into one row; the nightly enrichment Celery task
uses :func:`list_unenriched_artists` and :func:`mark_artist_enriched`
to keep Spotify-derived genres fresh.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from backend.core.text import normalize_artist_name
from backend.data.models.artists import Artist

if TYPE_CHECKING:
    import uuid
    from decimal import Decimal

    from sqlalchemy.orm import Session
    from sqlalchemy.sql import ColumnElement


def get_artist_by_id(session: Session, artist_id: uuid.UUID) -> Artist | None:
    """Fetch an artist by its primary key.

    Args:
        session: Active SQLAlchemy session.
        artist_id: UUID of the artist.

    Returns:
        The :class:`Artist` if found, else None.
    """
    return session.get(Artist, artist_id)


def list_artists_by_ids(session: Session, artist_ids: list[uuid.UUID]) -> list[Artist]:
    """Batch-fetch artists by a list of primary keys.

    Args:
        session: Active SQLAlchemy session.
        artist_ids: UUIDs to load.

    Returns:
        Artist rows in no guaranteed order. Missing IDs are omitted.
    """
    if not artist_ids:
        return []
    stmt = select(Artist).where(Artist.id.in_(artist_ids))
    return list(session.execute(stmt).scalars().all())


def get_artist_by_normalized_name(
    session: Session, normalized_name: str
) -> Artist | None:
    """Fetch an artist by its unique normalized lookup key.

    Args:
        session: Active SQLAlchemy session.
        normalized_name: Already-normalized lookup string.

    Returns:
        The :class:`Artist` if one exists under that key, else None.
    """
    stmt = select(Artist).where(Artist.normalized_name == normalized_name)
    return session.execute(stmt).scalar_one_or_none()


def upsert_artist_by_name(session: Session, raw_name: str) -> Artist:
    """Insert or fetch an artist row by its normalized name.

    The scraper runner calls this once per performer name it ingests.
    Because the normalized name is unique, repeated calls from the same
    or different scrapers collapse to one row, and the display-cased
    ``name`` is only written on insert (first spelling wins — a cheap
    stability property that keeps the UI from flipping casing every
    time a new source ingests the same artist).

    Args:
        session: Active SQLAlchemy session.
        raw_name: Artist name as it appeared in the scraped payload.

    Returns:
        The resolved :class:`Artist` row. Never None.
    """
    normalized = normalize_artist_name(raw_name)
    existing = get_artist_by_normalized_name(session, normalized)
    if existing is not None:
        return existing

    artist = Artist(
        name=raw_name.strip(),
        normalized_name=normalized,
        genres=[],
    )
    session.add(artist)
    session.flush()
    return artist


def list_unenriched_artists(session: Session, *, limit: int) -> list[Artist]:
    """Return artists that have never been through Spotify enrichment.

    Ordered by creation time so a backlog drains in the order it was
    built — newest scraped artists naturally wait behind older ones.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of artists to return.

    Returns:
        Up to ``limit`` :class:`Artist` rows whose
        ``spotify_enriched_at`` is NULL.
    """
    stmt = (
        select(Artist)
        .where(Artist.spotify_enriched_at.is_(None))
        .order_by(Artist.created_at.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def search_artists(session: Session, *, query: str, limit: int = 10) -> list[Artist]:
    """Return artists whose normalized name matches the given query.

    Uses case-insensitive substring match against ``normalized_name``.
    The query is normalized with the same primitive the ingestion path
    uses, so "beyoncé" and "BEYONCE" both find the same rows.

    Args:
        session: Active SQLAlchemy session.
        query: Raw search string from the user. Trimmed and normalized
            before use.
        limit: Maximum number of rows to return.

    Returns:
        Up to ``limit`` :class:`Artist` rows. Empty list for an empty or
        whitespace-only query.
    """
    stripped = query.strip()
    if not stripped:
        return []
    normalized = normalize_artist_name(stripped)
    if not normalized:
        return []
    pattern = f"%{normalized}%"
    stmt = (
        select(Artist)
        .where(Artist.normalized_name.ilike(pattern))
        .order_by(Artist.name.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def list_artists_for_musicbrainz_enrichment(
    session: Session,
    *,
    limit: int,
    stale_after: timedelta | None = None,
) -> list[Artist]:
    """Return artists that need a MusicBrainz enrichment pass.

    Selects artists whose ``musicbrainz_enriched_at`` is NULL — the
    primary backfill case — or whose timestamp is older than
    ``stale_after`` if a refresh interval is provided. Ordered by
    creation time so older artists drain first.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of artists to return.
        stale_after: Optional age threshold; rows enriched longer ago
            than this are considered for re-enrichment. ``None`` (the
            default) selects only rows that have never been enriched.

    Returns:
        Up to ``limit`` :class:`Artist` rows due for enrichment.
    """
    condition: ColumnElement[bool]
    if stale_after is None:
        condition = Artist.musicbrainz_enriched_at.is_(None)
    else:
        cutoff = datetime.now(UTC) - stale_after
        condition = or_(
            Artist.musicbrainz_enriched_at.is_(None),
            Artist.musicbrainz_enriched_at < cutoff,
        )
    stmt = (
        select(Artist).where(condition).order_by(Artist.created_at.asc()).limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def mark_artist_musicbrainz_enriched(
    session: Session,
    artist: Artist,
    *,
    musicbrainz_id: str | None,
    genres: list[dict[str, Any]] | None,
    tags: list[dict[str, Any]] | None,
    confidence: Decimal | None,
) -> Artist:
    """Persist the result of a MusicBrainz enrichment attempt.

    Always stamps ``musicbrainz_enriched_at`` so the nightly task does
    not re-check the same row on its next pass; callers indicate "no
    match found" by passing ``musicbrainz_id=None``. The genres and
    tags blobs are stored verbatim (with vote counts and original
    casing) because normalization is a separate future sprint.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` row being updated.
        musicbrainz_id: MBID of the matched MusicBrainz artist, or None
            when no candidate cleared the confidence threshold.
        genres: Raw ``genres`` array from the MusicBrainz API. ``None``
            or ``[]`` when there was no match.
        tags: Raw ``tags`` array from the MusicBrainz API. ``None`` or
            ``[]`` when there was no match.
        confidence: Match confidence in 0.00-1.00, or None for no match.

    Returns:
        The updated :class:`Artist` row (same instance, for convenience).
    """
    artist.musicbrainz_id = musicbrainz_id
    artist.musicbrainz_genres = genres
    artist.musicbrainz_tags = tags
    artist.musicbrainz_match_confidence = confidence
    artist.musicbrainz_enriched_at = datetime.now(UTC)
    session.flush()
    return artist


def mark_artist_enriched(
    session: Session,
    artist: Artist,
    *,
    spotify_id: str | None,
    genres: list[str],
) -> Artist:
    """Persist the result of a Spotify enrichment attempt.

    Always stamps ``spotify_enriched_at`` so the nightly task does not
    re-check the same row on its next pass; callers indicate "no match
    found" by passing ``spotify_id=None`` and ``genres=[]``.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` row being updated.
        spotify_id: Spotify artist id when enrichment found a high-
            confidence match, else None.
        genres: Canonical genre tags pulled off the Spotify payload.

    Returns:
        The updated :class:`Artist` row (same instance, for convenience).
    """
    artist.spotify_id = spotify_id
    artist.genres = genres
    artist.spotify_enriched_at = datetime.now(UTC)
    session.flush()
    return artist
