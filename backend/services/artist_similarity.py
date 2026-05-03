"""Storage and resolution for Last.fm similar-artist edges.

Operates on the ``artist_similarity`` table (Decision 059) the
:mod:`backend.services.lastfm_similarity_tasks` Celery task fills
nightly. Three concerns:

* :func:`store_similar_artists` upserts the edges Last.fm returned for
  one source artist. The source artist is the authority — rows present
  in the database but not in the new payload are deleted, so the table
  doesn't accumulate stale similarities. New rows attempt to resolve
  ``similar_artist_id`` immediately.

* :func:`resolve_similarity_links` is a periodic cleanup task. When the
  scraper introduces a new :class:`Artist` row, that artist may already
  appear as a ``similar_artist_name`` in existing similarity rows; this
  function backfills ``similar_artist_id`` on every such match.

* :func:`get_similar_artists` powers the magic query — "similar artists
  who have upcoming DMV shows" — by joining through ``similar_artist_id``
  to ``events``. Rows whose link could not be resolved are excluded
  from the city-filtered query, since a link is required to find shows.

Resolution uses MBID equality first, then case-insensitive whitespace-
normalized name equality. **No fuzzy matching.** False positives in
similarity links pollute recommendations more than false negatives do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, bindparam, cast, func, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY

from backend.core.logging import get_logger
from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.events import Event, EventStatus
from backend.data.models.venues import Venue

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.services.lastfm import LastFMSimilarArtist

logger = get_logger(__name__)

DEFAULT_SOURCE = "lastfm"
DEFAULT_LIMIT = 20

# Source label written to ``artist_similarity.source`` (or returned by
# the tag-overlap query) to distinguish tag-derived similarity from
# Last.fm collaborative similarity. The source enum on
# :class:`ArtistSimilarity` is informal (Decision 059), so adding a
# new value does not require a schema change.
TAG_OVERLAP_SOURCE = "tag_overlap"

# Default minimum number of shared tags required for a tag-overlap
# match to be useful. Below three, the Jaccard score becomes too noisy
# — two artists sharing one tag is a coincidence, three is a pattern.
DEFAULT_TAG_OVERLAP_MIN = 3

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ArtistSimilarityResult:
    """One row returned by :func:`get_similar_artists`.

    Attributes:
        similar_artist_name: Display name as stored on the similarity
            edge — the upstream provider's casing, not necessarily the
            casing on a matching :class:`Artist` row.
        similar_artist_id: UUID of the linked :class:`Artist` row, or
            None when the edge could not be resolved against the
            artists table.
        similar_artist_mbid: MusicBrainz ID stored on the edge, or None.
        similarity_score: Provider-reported similarity in 0.0-1.0.
        source: Provider that produced this edge (today: ``"lastfm"``).
        upcoming_show_count: Count of upcoming non-cancelled events
            featuring the linked artist in the requested city. Zero
            when ``only_with_upcoming_shows`` was not requested or no
            events were found.
    """

    similar_artist_name: str
    similar_artist_id: uuid.UUID | None
    similar_artist_mbid: str | None
    similarity_score: float
    source: str
    upcoming_show_count: int


@dataclass(frozen=True)
class TagSimilarityResult:
    """One artist returned by :func:`find_artists_by_tag_similarity`.

    Computed at query time from :attr:`Artist.granular_tags` overlap
    with the source artist; never persisted.

    Attributes:
        artist_id: UUID of the matching :class:`Artist` row.
        artist_name: Display name of the matching artist.
        shared_tag_count: Size of the tag intersection.
        total_tag_union: Size of the tag union — denominator of the
            Jaccard score.
        jaccard_score: ``|A ∩ B| / |A U B|`` in 0.0-1.0.
        upcoming_show_count: Count of upcoming non-cancelled events
            featuring the matching artist in the requested city. Zero
            when ``only_with_upcoming_shows`` was not requested.
    """

    artist_id: uuid.UUID
    artist_name: str
    shared_tag_count: int
    total_tag_union: int
    jaccard_score: float
    upcoming_show_count: int


def _name_lookup_key(name: str) -> str:
    """Normalize an artist name for case-insensitive comparison.

    Lower-cases and collapses internal whitespace so "LUCY  DACUS  "
    and "Lucy Dacus" compare equal. Deliberately lighter-weight than
    :func:`backend.core.text.normalize_artist_name` — diacritic
    stripping is too aggressive for the resolution path, where false
    positives are worse than false negatives.

    Args:
        name: Display-cased artist name.

    Returns:
        The lookup key used for resolution comparisons.
    """
    if not name:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", name).strip()
    return collapsed.lower()


def _build_resolution_index(
    session: Session,
    *,
    mbids: set[str],
    name_keys: set[str],
) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    """Fetch the artist rows needed to resolve a batch of edges.

    Avoids one round-trip per edge by pulling every candidate artist
    in two queries. Name keys are matched in Python after the fetch
    rather than via a SQL function, keeping the implementation portable
    across both Postgres (prod) and the in-memory engine the tests use
    when the assertion only depends on row identity.

    Args:
        session: Active SQLAlchemy session.
        mbids: MBID strings to look up.
        name_keys: Already-normalized lookup keys to match against
            artist display names.

    Returns:
        Two dicts: MBID → artist UUID, name key → artist UUID. Either
        may be empty when the input set is empty or no matches exist.
    """
    by_mbid: dict[str, uuid.UUID] = {}
    by_name: dict[str, uuid.UUID] = {}

    if mbids:
        stmt = select(Artist.id, Artist.musicbrainz_id).where(
            Artist.musicbrainz_id.in_(mbids)
        )
        for artist_id, mbid in session.execute(stmt).all():
            if mbid:
                by_mbid[mbid] = artist_id

    if name_keys:
        stmt2 = select(Artist.id, Artist.name)
        for artist_id, display_name in session.execute(stmt2).all():
            key = _name_lookup_key(display_name)
            if key in name_keys and key not in by_name:
                by_name[key] = artist_id

    return by_mbid, by_name


def _resolve_one(
    similar: LastFMSimilarArtist,
    *,
    by_mbid: dict[str, uuid.UUID],
    by_name: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    """Pick the best resolution candidate for one similarity entry.

    MBID match wins when both sides have an MBID. Otherwise falls back
    to the case-insensitive name key. Returns None if neither matches.

    Args:
        similar: The similarity record from Last.fm.
        by_mbid: Resolution index keyed by MBID.
        by_name: Resolution index keyed by name lookup key.

    Returns:
        The matching artist UUID, or None when no resolution exists.
    """
    if similar.mbid:
        match = by_mbid.get(similar.mbid)
        if match is not None:
            return match
    key = _name_lookup_key(similar.name)
    if key:
        return by_name.get(key)
    return None


def store_similar_artists(
    session: Session,
    source_artist_id: uuid.UUID,
    similar_artists: list[LastFMSimilarArtist],
    *,
    source: str = DEFAULT_SOURCE,
) -> None:
    """Upsert similar-artist relationships for a source artist.

    Existing rows for the same ``(source_artist_id, similar_artist_name,
    source)`` triple are updated with the new score. New rows get
    inserted. Rows present in the database but absent from the new
    payload are deleted — the source artist is the authority. The
    function does not commit; the caller decides the transaction
    boundary.

    For each similar artist, attempts to resolve ``similar_artist_id``
    by checking for a matching MBID first, then a case-insensitive
    exact name match. Sets ``similar_artist_id`` when found, leaves
    NULL when not.

    Args:
        session: Active SQLAlchemy session.
        source_artist_id: UUID of the artist we asked Last.fm about.
        similar_artists: Records returned by the upstream provider.
            Pass an empty list to clear all rows for this source.
        source: Provider identifier the rows are tagged with. Defaults
            to ``"lastfm"``.
    """
    incoming_names = {s.name for s in similar_artists if s.name}
    existing_stmt = select(ArtistSimilarity).where(
        ArtistSimilarity.source_artist_id == source_artist_id,
        ArtistSimilarity.source == source,
    )
    existing_rows = list(session.execute(existing_stmt).scalars().all())
    existing_by_name: dict[str, ArtistSimilarity] = {
        row.similar_artist_name: row for row in existing_rows
    }

    for stale in existing_rows:
        if stale.similar_artist_name not in incoming_names:
            session.delete(stale)

    if not similar_artists:
        session.flush()
        return

    mbids = {s.mbid for s in similar_artists if s.mbid}
    name_keys = {key for s in similar_artists if (key := _name_lookup_key(s.name))}
    by_mbid, by_name = _build_resolution_index(
        session, mbids=mbids, name_keys=name_keys
    )

    for entry in similar_artists:
        if not entry.name:
            continue
        resolved = _resolve_one(entry, by_mbid=by_mbid, by_name=by_name)
        score = Decimal(f"{entry.match_score:.3f}")
        existing = existing_by_name.get(entry.name)
        if existing is not None:
            existing.similarity_score = score
            existing.similar_artist_mbid = entry.mbid
            existing.similar_artist_id = resolved
            existing.updated_at = func.now()  # type: ignore[assignment]
        else:
            session.add(
                ArtistSimilarity(
                    source_artist_id=source_artist_id,
                    similar_artist_name=entry.name,
                    similar_artist_mbid=entry.mbid,
                    similar_artist_id=resolved,
                    similarity_score=score,
                    source=source,
                )
            )
    session.flush()


def resolve_similarity_links(session: Session) -> int:
    """Resolve ``similar_artist_id`` for previously unlinked rows.

    Runs as a periodic cleanup. When new artists are added to the
    database (typically by a scraper), some existing
    :class:`ArtistSimilarity` rows may now match. This function finds
    them and links them in a single batched pass.

    Resolution priority:

    1. ``similar_artist_mbid`` matches an artist's ``musicbrainz_id``.
    2. Case-insensitive whitespace-collapsed name match against
       ``Artist.name``.

    Does not attempt fuzzy matching — false positives pollute
    recommendations. Better to leave a row unlinked than to link it
    to the wrong artist.

    Args:
        session: Active SQLAlchemy session. Caller commits.

    Returns:
        The number of rows newly linked.
    """
    unresolved_stmt = select(
        ArtistSimilarity.id,
        ArtistSimilarity.similar_artist_name,
        ArtistSimilarity.similar_artist_mbid,
    ).where(ArtistSimilarity.similar_artist_id.is_(None))
    unresolved = list(session.execute(unresolved_stmt).all())
    if not unresolved:
        return 0

    mbids = {row.similar_artist_mbid for row in unresolved if row.similar_artist_mbid}
    name_keys = {
        key for row in unresolved if (key := _name_lookup_key(row.similar_artist_name))
    }
    by_mbid, by_name = _build_resolution_index(
        session, mbids=mbids, name_keys=name_keys
    )
    if not by_mbid and not by_name:
        return 0

    linked = 0
    for row in unresolved:
        artist_id: uuid.UUID | None = None
        if row.similar_artist_mbid:
            artist_id = by_mbid.get(row.similar_artist_mbid)
        if artist_id is None:
            key = _name_lookup_key(row.similar_artist_name)
            if key:
                artist_id = by_name.get(key)
        if artist_id is None:
            continue
        session.execute(
            update(ArtistSimilarity)
            .where(ArtistSimilarity.id == row.id)
            .values(similar_artist_id=artist_id, updated_at=func.now())
        )
        linked += 1

    session.flush()
    return linked


def get_similar_artists(
    session: Session,
    source_artist_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    only_with_upcoming_shows: bool = False,
    city_id: uuid.UUID | None = None,
    minimum_score: float = 0.0,
) -> list[ArtistSimilarityResult]:
    """Query similar artists for a source artist.

    Returns results sorted by similarity score descending. When
    ``only_with_upcoming_shows`` is True and ``city_id`` is provided,
    filters to similar artists who have an upcoming non-cancelled
    event whose ``artists`` list contains them in that city. This is
    the magic query — "artists like the ones you follow who are coming
    to DC."

    Similar artists with no resolved ``similar_artist_id`` are excluded
    when filtering by upcoming shows; the join requires the link.

    Args:
        session: Active SQLAlchemy session.
        source_artist_id: UUID of the artist whose similars to fetch.
        limit: Maximum number of results to return.
        only_with_upcoming_shows: When True, filters to artists with
            at least one upcoming non-cancelled event.
        city_id: City to scope the upcoming-shows filter to. Required
            when ``only_with_upcoming_shows`` is True; ignored
            otherwise.
        minimum_score: Drop edges whose similarity score is below this
            threshold. Defaults to 0.0 (return everything).

    Returns:
        Up to ``limit`` :class:`ArtistSimilarityResult` records sorted
        by score descending. Empty list when the source artist has no
        similarity rows or all rows are filtered out.
    """
    if only_with_upcoming_shows:
        return _get_similar_with_upcoming(
            session,
            source_artist_id=source_artist_id,
            city_id=city_id,
            limit=limit,
            minimum_score=minimum_score,
        )

    threshold = Decimal(f"{minimum_score:.3f}")
    stmt = (
        select(ArtistSimilarity)
        .where(ArtistSimilarity.source_artist_id == source_artist_id)
        .where(ArtistSimilarity.similarity_score >= threshold)
        .order_by(ArtistSimilarity.similarity_score.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        ArtistSimilarityResult(
            similar_artist_name=row.similar_artist_name,
            similar_artist_id=row.similar_artist_id,
            similar_artist_mbid=row.similar_artist_mbid,
            similarity_score=float(row.similarity_score),
            source=row.source,
            upcoming_show_count=0,
        )
        for row in rows
    ]


def _get_similar_with_upcoming(
    session: Session,
    *,
    source_artist_id: uuid.UUID,
    city_id: uuid.UUID | None,
    limit: int,
    minimum_score: float,
) -> list[ArtistSimilarityResult]:
    """Variant of :func:`get_similar_artists` that joins to upcoming events.

    Walks every resolved similarity row, fetches the linked artist's
    name, then counts upcoming events at venues in ``city_id`` whose
    performer list contains that name. Rows with zero matching events
    are filtered out.

    Args:
        session: Active SQLAlchemy session.
        source_artist_id: UUID of the source artist.
        city_id: City to scope the upcoming-shows filter to. ``None``
            returns an empty list — without a city scope every venue
            qualifies and the magic query loses its locality.
        limit: Maximum number of rows to return after filtering.
        minimum_score: Score threshold to apply before counting shows.

    Returns:
        Filtered, sorted, limited list.
    """
    if city_id is None:
        return []

    threshold = Decimal(f"{minimum_score:.3f}")
    similar_stmt = (
        select(ArtistSimilarity, Artist.name.label("artist_name"))
        .join(Artist, ArtistSimilarity.similar_artist_id == Artist.id)
        .where(ArtistSimilarity.source_artist_id == source_artist_id)
        .where(ArtistSimilarity.similarity_score >= threshold)
        .order_by(ArtistSimilarity.similarity_score.desc())
    )
    rows = session.execute(similar_stmt).all()
    if not rows:
        return []

    out: list[ArtistSimilarityResult] = []
    for sim_row, artist_name in rows:
        count_stmt = (
            select(func.count(Event.id))
            .join(Venue, Event.venue_id == Venue.id)
            .where(Venue.city_id == city_id)
            .where(Event.starts_at >= func.now())
            .where(Event.status != EventStatus.CANCELLED)
            .where(_event_features_artist_clause(artist_name))
        )
        show_count = session.execute(count_stmt).scalar_one() or 0
        if show_count <= 0:
            continue
        out.append(
            ArtistSimilarityResult(
                similar_artist_name=sim_row.similar_artist_name,
                similar_artist_id=sim_row.similar_artist_id,
                similar_artist_mbid=sim_row.similar_artist_mbid,
                similarity_score=float(sim_row.similarity_score),
                source=sim_row.source,
                upcoming_show_count=int(show_count),
            )
        )
        if len(out) >= limit:
            break
    return out


def _event_features_artist_clause(artist_name: str) -> Any:
    """Build a SQLAlchemy clause matching events that list ``artist_name``.

    Uses Postgres' ``ANY()`` array predicate against ``Event.artists``
    so the test happens entirely SQL-side. The casing of the stored
    display name has to match the value in the array — we don't try
    case-insensitive matching here because the resolution step already
    bound ``similar_artist_id`` to a specific :class:`Artist` row whose
    canonical name we use for the lookup.

    Args:
        artist_name: Display name to look for in ``Event.artists``.

    Returns:
        A SQLAlchemy boolean expression suitable for ``.where(...)``.
    """
    return cast(artist_name, String) == func.any(Event.artists)


def find_artists_by_tag_similarity(
    session: Session,
    source_artist_id: uuid.UUID,
    *,
    min_overlap: int = DEFAULT_TAG_OVERLAP_MIN,
    limit: int = DEFAULT_LIMIT,
    only_with_upcoming_shows: bool = False,
    city_id: uuid.UUID | None = None,
) -> list[TagSimilarityResult]:
    """Find artists with overlapping granular tags, ranked by Jaccard similarity.

    Computes ``|A ∩ B| / |A U B|`` between the source artist's
    ``granular_tags`` and every other artist's ``granular_tags`` at
    query time. The GIN index on ``granular_tags`` keeps the
    ``&&``-overlap pre-filter sub-linear; the per-row intersection
    cost is then bounded by the small candidate set that survives the
    pre-filter.

    The query passes the source artist's tags as a parameterized
    ``text[]`` rather than self-joining the artists table — this is
    measurably cheaper at scale and lets us short-circuit when the
    source has empty tags.

    Args:
        session: Active SQLAlchemy session.
        source_artist_id: UUID of the artist whose tag-similar peers
            we want.
        min_overlap: Minimum number of shared tags. Below this
            threshold, similarity is too weak to be useful — three
            shared tags is the floor where the signal beats noise.
        limit: Maximum number of results to return.
        only_with_upcoming_shows: When True with ``city_id``, restricts
            results to artists with at least one upcoming non-
            cancelled event in that city. This is the magic query —
            "tag-similar artists with shows in DC."
        city_id: Required when ``only_with_upcoming_shows`` is True.
            ``None`` returns an empty list without hitting the DB —
            without a city scope the magic query loses its locality.

    Returns:
        List of :class:`TagSimilarityResult` sorted by Jaccard score
        descending, then by ``upcoming_show_count`` descending as a
        tiebreaker. Results exclude the source artist, exclude artists
        with empty granular tags, and exclude rows below
        ``min_overlap``.
    """
    if only_with_upcoming_shows and city_id is None:
        return []

    source = session.get(Artist, source_artist_id)
    if source is None:
        return []
    source_tags: list[str] = list(source.granular_tags or [])
    if not source_tags:
        return []

    # PostgreSQL set operations on text arrays — uses ``unnest`` plus
    # ``INTERSECT`` for the intersection, then derives the union size
    # from ``|A| + |B| - |A ∩ B|``. The ``&&`` overlap predicate is
    # what the GIN index actually services; the intersection
    # cardinality is computed only on rows that survive the pre-filter.
    base_sql = """
        SELECT
          a.id,
          a.name,
          (SELECT COUNT(*) FROM (
              SELECT UNNEST(a.granular_tags)
              INTERSECT
              SELECT UNNEST(CAST(:source_tags AS text[]))
          ) AS shared_tags)::int AS shared_count,
          (
              cardinality(a.granular_tags)
              + cardinality(CAST(:source_tags AS text[]))
              - (SELECT COUNT(*) FROM (
                  SELECT UNNEST(a.granular_tags)
                  INTERSECT
                  SELECT UNNEST(CAST(:source_tags AS text[]))
              ) AS shared_for_union)
          )::int AS union_count
        FROM artists a
        WHERE a.id != :source_id
          AND a.granular_tags && CAST(:source_tags AS text[])
          AND cardinality(a.granular_tags) > 0
    """
    stmt = text(base_sql).bindparams(
        bindparam("source_tags", type_=ARRAY(String)),
        bindparam("source_id"),
    )
    rows = session.execute(
        stmt, {"source_tags": source_tags, "source_id": source_artist_id}
    ).all()

    candidates: list[TagSimilarityResult] = []
    for row in rows:
        shared = int(row.shared_count or 0)
        union = int(row.union_count or 0)
        if shared < min_overlap or union <= 0:
            continue
        jaccard = shared / union
        candidates.append(
            TagSimilarityResult(
                artist_id=row.id,
                artist_name=row.name,
                shared_tag_count=shared,
                total_tag_union=union,
                jaccard_score=jaccard,
                upcoming_show_count=0,
            )
        )

    if not candidates:
        return []

    if only_with_upcoming_shows:
        # Filter to only artists with upcoming events in the city, and
        # capture the count for tiebreaking.
        candidates = _attach_upcoming_show_counts(session, candidates, city_id=city_id)
        candidates = [c for c in candidates if c.upcoming_show_count > 0]

    candidates.sort(
        key=lambda r: (-r.jaccard_score, -r.upcoming_show_count, r.artist_name)
    )
    return candidates[:limit]


def _attach_upcoming_show_counts(
    session: Session,
    results: list[TagSimilarityResult],
    *,
    city_id: uuid.UUID | None,
) -> list[TagSimilarityResult]:
    """Replace each result with one carrying its upcoming-show count.

    Iterates the candidates sequentially because the input list is
    already small (capped by the GIN-prefiltered overlap query). For
    larger result sets this would warrant a single batched SQL pass,
    but at the query's natural scale a per-row count keeps the code
    readable.

    Args:
        session: Active SQLAlchemy session.
        results: Candidates produced by the tag-overlap pre-filter.
        city_id: City to scope the upcoming-shows filter to. ``None``
            returns the input unchanged with show counts left at zero.

    Returns:
        New list of :class:`TagSimilarityResult`, each carrying the
        count of upcoming non-cancelled events at venues in
        ``city_id`` whose performer list contains the artist's
        display name.
    """
    if city_id is None:
        return results

    out: list[TagSimilarityResult] = []
    for result in results:
        count_stmt = (
            select(func.count(Event.id))
            .join(Venue, Event.venue_id == Venue.id)
            .where(Venue.city_id == city_id)
            .where(Event.starts_at >= func.now())
            .where(Event.status != EventStatus.CANCELLED)
            .where(_event_features_artist_clause(result.artist_name))
        )
        show_count = int(session.execute(count_stmt).scalar_one() or 0)
        out.append(
            TagSimilarityResult(
                artist_id=result.artist_id,
                artist_name=result.artist_name,
                shared_tag_count=result.shared_tag_count,
                total_tag_union=result.total_tag_union,
                jaccard_score=result.jaccard_score,
                upcoming_show_count=show_count,
            )
        )
    return out
