"""Granular tag consolidation for artist similarity matching (Decision 060).

This module reads the per-source MusicBrainz and Last.fm tag payloads
collected by Sprints 1A and 1B and produces a single normalized,
deduplicated, frequency-trimmed list of *granular* tags per artist.
The output lives on :attr:`Artist.granular_tags` and powers the
tag-overlap similarity query that complements Last.fm collaborative
similarity for artists with thin Last.fm coverage.

This is intentionally separate from
:mod:`backend.services.genre_normalization`. That module collapses
many source tags into ~12 canonical labels — useful for filter chips
and high-level genre overlap. This module does the opposite: it
*preserves* discriminative resolution. "Indie folk" and "midwest emo"
mean different things to the recommendation engine, so we keep them
distinct rather than rolling both up into "Indie Rock".

**Two-pass architecture.** The document-frequency blocklist depends on
the consolidated output of every artist, so the very first run cannot
apply DF filtering — there is no blocklist yet. The backfill task
therefore runs twice: pass one populates ``granular_tags`` from raw
sources, then pass two rebuilds the blocklist using the freshly
populated data and re-consolidates every artist using it. Subsequent
runs reuse the cached blocklist (24h Redis TTL) and only re-consolidate
artists whose source data has moved on.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from backend.core.logging import get_logger
from backend.data.models.artists import Artist
from backend.services.genre_normalization import NOISE_TAG_PATTERNS

if TYPE_CHECKING:
    import uuid

    import redis
    from sqlalchemy.orm import Session

logger = get_logger(__name__)


# Tags appearing on this fraction of artists or more are too broad to
# discriminate. They're useful for canonical genres ("rock", "pop") but
# noise for similarity matching — every other artist also has them.
MAX_DOCUMENT_FREQUENCY = 0.30

# Minimum number of artists a tag must appear on to be useful.
# Tags on a single artist are noise — usually misspellings or one-user
# annotations.
MIN_GLOBAL_FREQUENCY = 3

# Maximum tags stored per artist. Beyond this, signal-to-noise drops:
# tail tags on a given artist tend to be one-user annotations or
# loosely-related sub-genres. Twenty is a reasonable per-artist budget
# that captures real diversity without bloating the GIN index.
MAX_TAGS_PER_ARTIST = 20

# Source weights for the per-tag score. MusicBrainz curated genres are
# editorial; MusicBrainz tags are user-generated but have vote counts;
# Last.fm tags are user-generated and ordered by popularity. The
# weights below pick up the rough hierarchy without trying to be too
# clever — the per-source weights matter less than the de-duplication
# that happens when the same tag appears in multiple sources.
_MB_GENRE_BASE_SCORE = 3.0
_MB_TAG_BASE_SCORE = 2.0
_LASTFM_TOP_BAND_SCORE = 2.5
_LASTFM_MID_BAND_SCORE = 1.5
_LASTFM_TAIL_SCORE = 1.0
_LASTFM_TOP_BAND_MAX_INDEX = 5
_LASTFM_MID_BAND_MAX_INDEX = 10

# Per-tag length sanity bounds. Tags shorter than 2 characters are
# almost always noise; tags longer than 50 are almost always garbage.
_MIN_TAG_LENGTH = 2
_MAX_TAG_LENGTH = 50

# Redis key + TTL for the document-frequency blocklist. The blocklist
# is stable across days, so a 24-hour cache keeps every per-artist
# consolidation cheap without staling out before the nightly refresh.
_BLOCKLIST_REDIS_KEY = "tag_consolidation:blocklist"
_BLOCKLIST_TTL_SECONDS = 24 * 60 * 60

# Patterns that mark a tag as purely temporal even when not in the
# noise list — covers years (e.g. ``"1997"``, ``"2014"``). Decade-only
# strings (``"90s"``) are already in :data:`NOISE_TAG_PATTERNS`.
_YEAR_RE = re.compile(r"^\d{4}$")

# Pure geographic descriptors — extends the smaller list in
# :data:`NOISE_TAG_PATTERNS` with location-only tags that show up in
# the Last.fm tail and would otherwise dominate similarity for any
# artist with strong local-scene roots.
_GEOGRAPHIC_ONLY_TAGS: frozenset[str] = frozenset(
    {
        "dc",
        "washington dc",
        "washington",
        "nyc",
        "new york",
        "la",
        "los angeles",
        "chicago",
        "seattle",
        "portland",
        "atlanta",
        "boston",
        "philadelphia",
        "philly",
        "uk",
        "us",
        "usa",
        "british",
        "english",
        "scottish",
        "irish",
        "american",
        "australian",
        "japanese",
        "german",
        "french",
        "italian",
        "spanish",
        "swedish",
        "norwegian",
        "canadian",
        "european",
        "asian",
        "african",
        "international",
    }
)


def normalize_tag(raw_tag: str) -> str:
    """Normalize a raw tag string for storage and matching.

    Steps:

    * Lowercase and strip outer whitespace.
    * Replace underscores with spaces (Last.fm sometimes returns
      ``hip_hop`` rather than ``hip hop``).
    * Collapse multiple internal spaces to a single space.
    * Reject tags shorter than two characters or longer than fifty
      after normalization (likely garbage).

    The output is intentionally close to display form rather than a
    slugified ASCII variant — the granular tags are searched by
    Postgres array operators on text equality, so the normalized form
    needs to be deterministic across sources but readable at debug
    time.

    Args:
        raw_tag: Raw tag string from a source payload.

    Returns:
        The normalized tag, or empty string when the input is invalid
        after normalization.
    """
    if not isinstance(raw_tag, str):
        return ""
    cleaned = raw_tag.lower().strip().replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < _MIN_TAG_LENGTH or len(cleaned) > _MAX_TAG_LENGTH:
        return ""
    return cleaned


def is_useful_for_similarity(tag: str) -> bool:
    """Return True when a normalized tag is useful for similarity matching.

    Filters out:

    * Listening-habit tags ("seen live", "favorites", "love") — pulled
      from :data:`NOISE_TAG_PATTERNS` so the rules stay in lock-step
      with the canonical genre normalizer.
    * Pure year tags (``"1997"``, ``"2024"``) and pure decade tags
      (``"90s"``, ``"2010s"``).
    * Pure geographic descriptors with no genre content (``"british"``,
      ``"dc"``).
    * Tags shorter than 2 characters or longer than 50.

    This is a per-tag filter only. Document-frequency filtering happens
    later in the database pass via :func:`build_global_tag_blocklist`.

    Args:
        tag: A tag already passed through :func:`normalize_tag`.

    Returns:
        True when the tag should be retained for similarity matching,
        False when it should be discarded.
    """
    if not tag or len(tag) < _MIN_TAG_LENGTH or len(tag) > _MAX_TAG_LENGTH:
        return False
    if tag in NOISE_TAG_PATTERNS:
        return False
    if tag in _GEOGRAPHIC_ONLY_TAGS:
        return False
    return not _YEAR_RE.match(tag)


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort int coercion that never raises.

    MusicBrainz returns ``count`` as an int, but Last.fm sometimes
    returns it as a string. We normalize both shapes to a non-negative
    integer; anything unparseable becomes ``default``.

    Args:
        value: The candidate value.
        default: Fallback when coercion fails.

    Returns:
        The coerced integer, or ``default``.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _score_musicbrainz_entries(
    entries: list[dict[str, Any]] | None,
    *,
    base_score: float,
) -> dict[str, float]:
    """Score MusicBrainz genre or tag entries by name.

    Each entry's per-tag score is ``base_score`` plus a vote-count
    bonus capped at ``+1.0``. The vote bonus is logarithmic so a
    tag with 50 votes does not get scored 50x a tag with 1 vote — the
    diminishing-returns shape mirrors :func:`genre_normalization._vote_weight`.

    Args:
        entries: Raw list of ``{"name": str, "count": int}`` dicts.
        base_score: The score floor for a vote-count of zero.

    Returns:
        Mapping of normalized tag → score.
    """
    if not entries:
        return {}
    scores: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        normalized = normalize_tag(name)
        if not normalized or not is_useful_for_similarity(normalized):
            continue
        count = max(0, _coerce_int(entry.get("count", 0)))
        bonus = min(1.0, math.log2(count + 1) / 6.0) if count > 0 else 0.0
        score = base_score + bonus
        if score > scores.get(normalized, 0.0):
            scores[normalized] = score
    return scores


def _score_lastfm_tags(
    tags: list[dict[str, Any]] | None,
) -> dict[str, float]:
    """Score Last.fm tag entries by popularity-rank position.

    Last.fm tag arrays are ordered by popularity. The top 5 carry the
    highest weight, the next 5 carry less, and the long tail counts
    once. This mirrors the :mod:`backend.services.genre_normalization`
    weighting so the two consumers agree on which tags are signal-rich.

    Args:
        tags: Raw list of ``{"name": str, "url": str}`` dicts.

    Returns:
        Mapping of normalized tag → score.
    """
    if not tags:
        return {}
    scores: dict[str, float] = {}
    for index, entry in enumerate(tags):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        normalized = normalize_tag(name)
        if not normalized or not is_useful_for_similarity(normalized):
            continue
        if index < _LASTFM_TOP_BAND_MAX_INDEX:
            score = _LASTFM_TOP_BAND_SCORE
        elif index < _LASTFM_MID_BAND_MAX_INDEX:
            score = _LASTFM_MID_BAND_SCORE
        else:
            score = _LASTFM_TAIL_SCORE
        if score > scores.get(normalized, 0.0):
            scores[normalized] = score
    return scores


def extract_artist_tags(
    musicbrainz_genres: list[dict[str, Any]] | None,
    musicbrainz_tags: list[dict[str, Any]] | None,
    lastfm_tags: list[dict[str, Any]] | None,
) -> list[str]:
    """Extract and rank an artist's discriminative tags from raw source data.

    Pipeline:

    1. Score each source independently via
       :func:`_score_musicbrainz_entries` (genres higher than tags) and
       :func:`_score_lastfm_tags` (top 5 highest, then mid-band, then
       tail).
    2. When the same normalized tag appears in multiple sources, sum
       its source scores. Tags surfaced by both sources are stronger
       signals than tags appearing in only one.
    3. Sort by total score descending, alphabetically as a tiebreaker
       so the output is deterministic across runs.
    4. Cap at :data:`MAX_TAGS_PER_ARTIST`.

    The MusicBrainz genre source carries the highest base score, so
    when an artist has clean MusicBrainz data the top of the output
    list will be MB-curated genres; Last.fm fills in for artists where
    MusicBrainz has nothing.

    Args:
        musicbrainz_genres: Raw ``genres`` payload from
            :attr:`Artist.musicbrainz_genres`. ``None`` is treated as
            no data.
        musicbrainz_tags: Raw ``tags`` payload from
            :attr:`Artist.musicbrainz_tags`. ``None`` is treated as no
            data.
        lastfm_tags: Raw ``tag`` payload from
            :attr:`Artist.lastfm_tags`. ``None`` is treated as no data.

    Returns:
        Ordered list of normalized tags, score-descending, capped at
        :data:`MAX_TAGS_PER_ARTIST`. Empty list when every source is
        empty or every tag was filtered out.
    """
    mb_genres = _score_musicbrainz_entries(
        musicbrainz_genres, base_score=_MB_GENRE_BASE_SCORE
    )
    mb_tags = _score_musicbrainz_entries(
        musicbrainz_tags, base_score=_MB_TAG_BASE_SCORE
    )
    lfm = _score_lastfm_tags(lastfm_tags)

    combined: dict[str, float] = {}
    for source in (mb_genres, mb_tags, lfm):
        for tag, score in source.items():
            combined[tag] = combined.get(tag, 0.0) + score

    if not combined:
        return []

    ordered = sorted(combined.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ordered[:MAX_TAGS_PER_ARTIST]]


def build_global_tag_blocklist(
    session: Session,
    redis_client: redis.Redis | None = None,
) -> set[str]:
    """Build the document-frequency blocklist of overly broad tags.

    Counts how many artists each tag appears on across the freshly-
    populated ``granular_tags`` column. Tags appearing on more than
    :data:`MAX_DOCUMENT_FREQUENCY` of all artists with non-empty
    granular tags are considered too broad to discriminate and are
    added to the blocklist. Tags appearing on fewer than
    :data:`MIN_GLOBAL_FREQUENCY` artists are also blocked — they're
    almost always misspellings or single-user annotations.

    The result is cached in Redis for 24 hours under
    :data:`_BLOCKLIST_REDIS_KEY`. Callers that pass a Redis client
    benefit from the cache; callers that pass ``None`` always recompute
    (used during the bootstrapping pass and in tests).

    Run order matters: this function is called *after* an initial
    consolidation pass populates ``granular_tags`` for the first time.
    The first pass cannot DF-filter because there's nothing to count.

    Args:
        session: Active SQLAlchemy session.
        redis_client: Optional Redis client for caching. Pass ``None``
            to skip caching and always recompute.

    Returns:
        Set of normalized tag names that should be excluded from
        ``granular_tags`` arrays. Empty set when no artists have any
        granular tags yet.
    """
    if redis_client is not None:
        cached = redis_client.get(_BLOCKLIST_REDIS_KEY)
        if cached:
            try:
                if isinstance(cached, bytes):
                    text = cached.decode("utf-8")
                else:
                    text = str(cached)
                return {line for line in text.split("\n") if line}
            except (UnicodeDecodeError, AttributeError):
                # Fall through to recompute on cache corruption.
                pass

    # Total artists with at least one granular tag — the denominator
    # for the document-frequency calculation.
    total_stmt = (
        select(func.count())
        .select_from(Artist)
        .where(func.coalesce(func.array_length(Artist.granular_tags, 1), 0) > 0)
    )
    total_artists = session.execute(total_stmt).scalar_one() or 0
    if total_artists == 0:
        return set()

    # Per-tag artist counts via ``unnest`` — Postgres reports the
    # number of distinct artists each tag appears on by grouping over
    # the unnested tag column.
    tag_column = func.unnest(Artist.granular_tags).label("tag")
    counts_stmt = (
        select(tag_column, func.count().label("freq"))
        .select_from(Artist)
        .group_by("tag")
    )
    df_threshold = total_artists * MAX_DOCUMENT_FREQUENCY
    blocklist: set[str] = set()
    for tag, freq in session.execute(counts_stmt).all():
        if not isinstance(tag, str):
            continue
        if freq < MIN_GLOBAL_FREQUENCY:
            blocklist.add(tag)
            continue
        if freq >= df_threshold:
            blocklist.add(tag)

    if redis_client is not None and blocklist:
        redis_client.setex(
            _BLOCKLIST_REDIS_KEY,
            _BLOCKLIST_TTL_SECONDS,
            "\n".join(sorted(blocklist)).encode("utf-8"),
        )

    return blocklist


def consolidate_artist_tags(
    session: Session,
    artist_id: uuid.UUID,
    *,
    blocklist: set[str] | None = None,
) -> list[str]:
    """Consolidate one artist's tags into the ``granular_tags`` column.

    Reads :attr:`Artist.musicbrainz_genres`,
    :attr:`Artist.musicbrainz_tags`, and :attr:`Artist.lastfm_tags`,
    runs them through :func:`extract_artist_tags`, applies the
    document-frequency blocklist when supplied, and writes the result
    to :attr:`Artist.granular_tags`. Always stamps
    :attr:`Artist.granular_tags_consolidated_at` so the nightly task
    can detect already-current rows on the next pass.

    Args:
        session: Active SQLAlchemy session. Caller commits.
        artist_id: UUID of the artist to consolidate.
        blocklist: Pre-computed document-frequency blocklist to apply.
            ``None`` skips DF-filtering — used on the bootstrapping
            first pass before any blocklist exists.

    Returns:
        The final tag list written to the artist row. Empty list when
        the artist has no usable source data.
    """
    artist = session.get(Artist, artist_id)
    if artist is None:
        return []

    candidates = extract_artist_tags(
        artist.musicbrainz_genres,
        artist.musicbrainz_tags,
        artist.lastfm_tags,
    )
    if blocklist:
        candidates = [tag for tag in candidates if tag not in blocklist]

    artist.granular_tags = candidates
    artist.granular_tags_consolidated_at = datetime.now(UTC)
    session.flush()
    return candidates
