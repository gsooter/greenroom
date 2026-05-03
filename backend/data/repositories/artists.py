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
from backend.data.models.artist_similarity import ArtistSimilarity
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


def list_artists_for_lastfm_enrichment(
    session: Session,
    *,
    limit: int,
    stale_after: timedelta | None = None,
) -> list[Artist]:
    """Return artists that need a Last.fm enrichment pass.

    Selects artists whose ``lastfm_enriched_at`` is NULL — the primary
    backfill case — or whose timestamp is older than ``stale_after``
    if a refresh interval is provided. Ordered by creation time so
    older artists drain first.

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
        condition = Artist.lastfm_enriched_at.is_(None)
    else:
        cutoff = datetime.now(UTC) - stale_after
        condition = or_(
            Artist.lastfm_enriched_at.is_(None),
            Artist.lastfm_enriched_at < cutoff,
        )
    stmt = (
        select(Artist).where(condition).order_by(Artist.created_at.asc()).limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def mark_artist_lastfm_enriched(
    session: Session,
    artist: Artist,
    *,
    tags: list[dict[str, Any]] | None,
    listener_count: int | None,
    url: str | None,
    bio_summary: str | None,
    confidence: Decimal | None,
) -> Artist:
    """Persist the result of a Last.fm enrichment attempt.

    Always stamps ``lastfm_enriched_at`` so the nightly task does not
    re-check the same row on its next pass; callers indicate "no match
    found" by passing ``tags=None``. The tags blob is stored verbatim
    (preserving order and URLs) because normalization happens in a
    separate sprint.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` row being updated.
        tags: Raw ``tag`` array from Last.fm. ``None`` or ``[]`` when
            there was no match.
        listener_count: Last.fm listener count for the matched artist,
            or None when no match.
        url: Canonical Last.fm artist page URL, or None when no match.
        bio_summary: Short artist bio blurb returned by Last.fm, or None.
        confidence: Match confidence in 0.00-1.00, or None for no match.

    Returns:
        The updated :class:`Artist` row (same instance, for convenience).
    """
    artist.lastfm_tags = tags
    artist.lastfm_listener_count = listener_count
    artist.lastfm_url = url
    artist.lastfm_bio_summary = bio_summary
    artist.lastfm_match_confidence = confidence
    artist.lastfm_enriched_at = datetime.now(UTC)
    session.flush()
    return artist


def list_artists_for_genre_normalization(
    session: Session,
    *,
    limit: int,
    force: bool = False,
) -> list[Artist]:
    """Return artists whose canonical genre output is stale or missing.

    Three buckets qualify under default selection: rows that have never
    been normalized, rows whose MusicBrainz enrichment is more recent
    than the last normalization, and rows whose Last.fm enrichment is
    more recent than the last normalization. ``force=True`` ignores the
    timestamp comparisons and returns up to ``limit`` rows in creation
    order — useful when the canonical mapping dictionary changes and
    every artist needs a re-run.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of artists to return.
        force: When True, return any artist (oldest first) regardless
            of whether their canonical genres are already current.

    Returns:
        Up to ``limit`` :class:`Artist` rows due for normalization.
    """
    if force:
        stmt = select(Artist).order_by(Artist.created_at.asc()).limit(limit)
        return list(session.execute(stmt).scalars().all())

    condition = or_(
        Artist.genres_normalized_at.is_(None),
        Artist.musicbrainz_enriched_at > Artist.genres_normalized_at,
        Artist.lastfm_enriched_at > Artist.genres_normalized_at,
    )
    stmt = (
        select(Artist).where(condition).order_by(Artist.created_at.asc()).limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def list_artist_names_with_canonical_genres(
    session: Session, genres: list[str]
) -> list[str]:
    """Return display names of artists whose canonical genres overlap input.

    Backs the ``GET /api/v1/events?genres=`` filter — the events query
    asks "which artists play any of these canonical genres" first, then
    matches those names against ``Event.artists``. The GIN index on
    ``canonical_genres`` makes the overlap check cheap even at full
    catalog scale.

    Args:
        session: Active SQLAlchemy session.
        genres: Canonical genre labels to match against
            :attr:`Artist.canonical_genres`.

    Returns:
        Display names of every artist whose canonical genre list
        intersects ``genres``. Empty list when ``genres`` is empty or no
        artist matches.
    """
    if not genres:
        return []
    stmt = (
        select(Artist.name)
        .where(Artist.canonical_genres.is_not(None))
        .where(Artist.canonical_genres.op("&&")(genres))
    )
    return list(session.execute(stmt).scalars().all())


def get_canonical_genres_by_normalized_name(
    session: Session, normalized_names: list[str]
) -> dict[str, list[str]]:
    """Return a normalized-name → canonical genres map for the given names.

    Used by the recommendation engine to pre-fetch the canonical genres
    of every artist named on a candidate event. The scorer reads this
    map keyed by :func:`backend.core.text.normalize_artist_name` so it
    can collapse "Phoebe Bridgers" / "phoebe bridgers" / "PHOEBE
    BRIDGERS" to one lookup.

    Only rows with a non-empty ``canonical_genres`` are returned —
    artists that have been normalized but produced no canonical mapping
    are omitted, since they can't contribute to genre overlap anyway.

    Args:
        session: Active SQLAlchemy session.
        normalized_names: Already-normalized artist lookup keys.

    Returns:
        Mapping of normalized name to canonical genres list. Empty dict
        when ``normalized_names`` is empty or no matching artist has any
        canonical genres.
    """
    if not normalized_names:
        return {}
    stmt = select(Artist.normalized_name, Artist.canonical_genres).where(
        Artist.normalized_name.in_(normalized_names),
        Artist.canonical_genres.is_not(None),
    )
    out: dict[str, list[str]] = {}
    for normalized, canonical in session.execute(stmt).all():
        if canonical:
            out[normalized] = list(canonical)
    return out


def mark_artist_genres_normalized(
    session: Session,
    artist: Artist,
    *,
    canonical_genres: list[str],
    genre_confidence: dict[str, float],
) -> Artist:
    """Persist the result of a genre normalization pass.

    Always stamps ``genres_normalized_at`` so the nightly task does not
    re-process the same row until either source enrichment moves on.
    Empty results (``[]`` and ``{}``) are stored verbatim — they encode
    "we ran the normalizer and found no canonical mapping" rather than
    "we never ran the normalizer", which the timestamp also captures.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` row being updated.
        canonical_genres: Ordered list of canonical genre labels.
            Empty list when no canonical mapping was possible.
        genre_confidence: Per-genre confidence map mirroring
            ``canonical_genres``. Empty dict when no mapping was
            possible.

    Returns:
        The updated :class:`Artist` row (same instance, for convenience).
    """
    artist.canonical_genres = canonical_genres
    artist.genre_confidence = genre_confidence
    artist.genres_normalized_at = datetime.now(UTC)
    session.flush()
    return artist


def list_artists_for_lastfm_similar_enrichment(
    session: Session,
    *,
    limit: int,
    stale_after: timedelta | None = None,
) -> list[Artist]:
    """Return artists that need a Last.fm similar-artists enrichment pass.

    Selects artists whose ``lastfm_similar_enriched_at`` is NULL — the
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
        Up to ``limit`` :class:`Artist` rows due for similarity
        enrichment.
    """
    condition: ColumnElement[bool]
    if stale_after is None:
        condition = Artist.lastfm_similar_enriched_at.is_(None)
    else:
        cutoff = datetime.now(UTC) - stale_after
        condition = or_(
            Artist.lastfm_similar_enriched_at.is_(None),
            Artist.lastfm_similar_enriched_at < cutoff,
        )
    stmt = (
        select(Artist).where(condition).order_by(Artist.created_at.asc()).limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def mark_artist_lastfm_similar_enriched(
    session: Session,
    artist: Artist,
) -> Artist:
    """Stamp the Last.fm similarity enrichment timestamp on an artist row.

    Called on every enrichment attempt — including no-match outcomes —
    so the nightly task does not re-search the same row on its next
    pass. The actual similarity rows are written by
    :func:`backend.services.artist_similarity.store_similar_artists`;
    this helper only flips the gating column.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` row being updated.

    Returns:
        The updated :class:`Artist` row (same instance, for convenience).
    """
    artist.lastfm_similar_enriched_at = datetime.now(UTC)
    session.flush()
    return artist


def list_similar_artists_for_anchors(
    session: Session,
    anchor_artist_ids: list[uuid.UUID],
    *,
    minimum_score: float = 0.0,
) -> list[tuple[uuid.UUID, str, float]]:
    """Return similarity edges for a batch of anchor artist UUIDs.

    Used by the recommendation engine to assemble the per-anchor
    similar-artist lookup map the
    :class:`backend.recommendations.scorers.similar_artist.SimilarArtistScorer`
    needs. A single query covers every anchor; the engine fans the
    result back out into a per-anchor dict.

    Args:
        session: Active SQLAlchemy session.
        anchor_artist_ids: UUIDs of the user's anchor artists (followed
            artists plus connected music-service top artists). Empty
            list returns an empty list without hitting the database.
        minimum_score: Drop edges below this similarity score before
            returning. Defaults to 0.0 (return everything).

    Returns:
        List of ``(source_artist_id, similar_artist_name,
        similarity_score)`` tuples ordered by source then similarity
        score descending. Empty when the input is empty or no rows
        match the threshold.
    """
    if not anchor_artist_ids:
        return []
    from decimal import Decimal

    threshold = Decimal(f"{minimum_score:.3f}")
    stmt = (
        select(
            ArtistSimilarity.source_artist_id,
            ArtistSimilarity.similar_artist_name,
            ArtistSimilarity.similarity_score,
        )
        .where(ArtistSimilarity.source_artist_id.in_(anchor_artist_ids))
        .where(ArtistSimilarity.similarity_score >= threshold)
        .order_by(
            ArtistSimilarity.source_artist_id,
            ArtistSimilarity.similarity_score.desc(),
        )
    )
    return [
        (source_id, name, float(score))
        for source_id, name, score in session.execute(stmt).all()
    ]


def list_artist_ids_by_normalized_names(
    session: Session,
    normalized_names: list[str],
) -> dict[str, uuid.UUID]:
    """Map normalized artist names to their UUIDs in one round-trip.

    Used by the recommendation engine to attach UUIDs to the user's
    music-service top-artist names so they can be looked up in the
    similarity table. Names are matched against ``normalized_name``
    only; case/diacritic normalization is the caller's responsibility.

    Args:
        session: Active SQLAlchemy session.
        normalized_names: Pre-normalized lookup keys.

    Returns:
        Mapping of normalized name → artist UUID. Empty when the input
        is empty or no matches exist.
    """
    if not normalized_names:
        return {}
    stmt = select(Artist.normalized_name, Artist.id).where(
        Artist.normalized_name.in_(normalized_names)
    )
    return {name: artist_id for name, artist_id in session.execute(stmt).all()}


def list_artists_for_tag_consolidation(
    session: Session,
    *,
    limit: int,
    force: bool = False,
) -> list[Artist]:
    """Return artists whose granular-tags consolidation is stale or missing.

    Default mode picks rows that have never been consolidated, plus
    rows whose source enrichment timestamps (MusicBrainz or Last.fm)
    have moved on since the last consolidation. ``force=True`` ignores
    the timestamp comparisons and returns up to ``limit`` artists in
    creation order — used by the second-pass blocklist application
    inside :mod:`backend.services.tag_consolidation_tasks`, where every
    artist must be re-consolidated against the freshly-built blocklist
    even if their source data hasn't changed.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of artists to return.
        force: When True, return any artist (oldest first) regardless
            of consolidation freshness.

    Returns:
        Up to ``limit`` :class:`Artist` rows due for consolidation.
    """
    if force:
        stmt = select(Artist).order_by(Artist.created_at.asc()).limit(limit)
        return list(session.execute(stmt).scalars().all())

    condition = or_(
        Artist.granular_tags_consolidated_at.is_(None),
        Artist.musicbrainz_enriched_at > Artist.granular_tags_consolidated_at,
        Artist.lastfm_enriched_at > Artist.granular_tags_consolidated_at,
    )
    stmt = (
        select(Artist).where(condition).order_by(Artist.created_at.asc()).limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


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
