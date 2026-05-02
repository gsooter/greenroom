"""Tag cleanup, mapping, and confidence-weighted merge for canonical genres.

This module merges the per-source genre signals collected by Sprints 1A
and 1B (MusicBrainz curated genres + tags, Last.fm user tags) into the
short, opinionated GREENROOM canonical genre list defined in
:mod:`backend.core.genres`. The output (an ordered list of canonical
labels plus a per-genre confidence score in 0.0-1.0) is what the
recommendation engine and the events genre filter read.

Three stages run in order:

1. **Cleanup and filtering.** :func:`clean_tag` lowercases, strips, and
   normalizes underscores. :func:`is_noise_tag` drops listening-habit
   tags ("seen live"), pure geography ("british"), pure decade ("90s"),
   meta tags ("music"), and personal annotations ("lol") so they never
   reach the mapper.
2. **Tag-to-canonical mapping.** :data:`GENRE_MAPPING` is a curated
   dictionary from canonical genre to substring patterns. Patterns are
   case-insensitive substring matches against cleaned tags. A tag that
   matches several patterns inside the same canonical counts once for
   that canonical; a tag that matches across canonicals counts once for
   each.
3. **Confidence-weighted merge.** :func:`normalize_genres` weights
   MusicBrainz signal at 1.5x and Last.fm at 1.0x (curated vs user-
   generated, see Decision 058), sums the weighted hit counts per
   canonical, and rescales relative to the strongest canonical for that
   artist. Confidence is therefore "how strong is this genre signal
   compared to the strongest signal for this artist", which is
   comparable across artists in a way absolute counts are not.

**Mapping rules.**

* Patterns are case-insensitive substring matches against cleaned tags.
* More specific patterns appear before more general ones inside each
  list — first match wins for tiebreakers.
* The mapping is intentionally conservative: when in doubt, leave a tag
  unmapped rather than miscategorize. False positives dilute the
  recommendation chips faster than missing an occasional match does.
* Adding a new pattern is safe; removing one may invalidate previously
  normalized data. Re-run the backfill with ``force=True`` if you
  reshape an existing entry.
"""

from __future__ import annotations

import math
from typing import Any

from backend.core.genres import GENRE_LABELS

MIN_CONFIDENCE_THRESHOLD = 0.5
MAX_CANONICAL_GENRES = 5

# Source weighting. MusicBrainz is curated and editorial; Last.fm is
# user-applied and noisier. The 1.5x edge keeps MusicBrainz from being
# drowned out by Last.fm's larger but messier tag stream while still
# letting Last.fm break ties when MusicBrainz is silent.
SOURCE_WEIGHTS: dict[str, float] = {
    "musicbrainz": 1.5,
    "lastfm": 1.0,
}

# Last.fm bucket weights — top 5 (popularity-ordered) carry 3x weight,
# next 5 carry 2x, and the long tail counts once. Last.fm tag arrays
# are popularity-ranked, so the head dominates the signal.
_LASTFM_TOP_WEIGHT = 3.0
_LASTFM_MID_WEIGHT = 2.0
_LASTFM_TAIL_WEIGHT = 1.0
_LASTFM_TOP_BAND = 5
_LASTFM_MID_BAND = 10

# Tags we never want to mix into the canonical merge. Listening-habit
# annotations, pure geography/era markers, meta tags, and slang are all
# popular enough on Last.fm that they can swamp a real genre signal.
NOISE_TAG_PATTERNS: frozenset[str] = frozenset(
    {
        # Listening habits
        "seen live",
        "favorite",
        "favorites",
        "favourite",
        "favourites",
        "love",
        "loved",
        "amazing",
        "awesome",
        "best",
        "good",
        "great",
        # Geographic only
        "british",
        "american",
        "english",
        "japanese",
        "german",
        "french",
        "uk",
        "us",
        "usa",
        # Temporal only
        "00s",
        "90s",
        "80s",
        "70s",
        "60s",
        "2000s",
        "1990s",
        "2010s",
        "2020s",
        # Meta
        "music",
        "artist",
        "band",
        "singer",
        "songwriter",
        "male vocalists",
        "female vocalists",
        # Personal annotations
        "lol",
        "haha",
        "wtf",
        "yes",
        "no",
    }
)

# Curated mapping of canonical GREENROOM genres to substring patterns.
# The keys must remain in lock-step with :data:`backend.core.genres.GENRE_LABELS`
# values; a runtime guard at the bottom of this module asserts that.
GENRE_MAPPING: dict[str, list[str]] = {
    "Indie Rock": [
        "indie rock",
        "indie",
        "alternative rock",
        "alt rock",
        "indie pop rock",
        "lo-fi",
        "lo fi",
        "garage rock",
        "post-punk",
        "post punk",
        "shoegaze",
        "dream pop",
        "noise rock",
        "math rock",
        "emo",
        "midwest emo",
    ],
    "Hip Hop": [
        "hip hop",
        "hip-hop",
        "rap",
        "trap",
        "drill",
        "boom bap",
        "conscious hip hop",
        "alternative hip hop",
        "gangsta rap",
        "underground hip hop",
        "experimental hip hop",
    ],
    "Electronic": [
        "electronic",
        "edm",
        "house",
        "techno",
        "ambient",
        "drum and bass",
        "dnb",
        "drum n bass",
        "dubstep",
        "downtempo",
        "idm",
        "synthwave",
        "electro",
        "trance",
        "garage",
        "uk garage",
        "breakbeat",
    ],
    "Jazz": [
        "jazz",
        "bebop",
        "free jazz",
        "fusion",
        "smooth jazz",
        "jazz fusion",
        "modal jazz",
        "hard bop",
        "cool jazz",
        "avant-garde jazz",
        "vocal jazz",
        "swing",
    ],
    "R&B": [
        "r&b",
        "rnb",
        "rhythm and blues",
        "neo-soul",
        "neo soul",
        "contemporary r&b",
        "alternative r&b",
        "soul",
    ],
    "Folk": [
        "folk",
        "indie folk",
        "folk rock",
        "americana",
        "acoustic",
        "singer-songwriter",
        "singer songwriter",
        "freak folk",
        "anti-folk",
        "folk punk",
        "country folk",
    ],
    "Metal": [
        "metal",
        "heavy metal",
        "death metal",
        "black metal",
        "thrash metal",
        "doom metal",
        "sludge",
        "metalcore",
        "post-metal",
        "progressive metal",
        "stoner metal",
    ],
    "Pop": [
        "pop",
        "synth-pop",
        "synth pop",
        "indie pop",
        "art pop",
        "dance pop",
        "electropop",
        "k-pop",
        "j-pop",
        "bedroom pop",
        "hyperpop",
        "chamber pop",
    ],
    "Funk/Soul": [
        "funk",
        "soul",
        "funk soul",
        "p-funk",
        "psychedelic soul",
        "disco",
        "boogie",
        "go-go",
        "go go",
    ],
    "Classical": [
        "classical",
        "contemporary classical",
        "minimalism",
        "modern classical",
        "baroque",
        "chamber music",
        "orchestral",
        "opera",
    ],
    "Punk": [
        "punk",
        "punk rock",
        "hardcore",
        "hardcore punk",
        "post-hardcore",
        "pop punk",
        "skate punk",
        "anarcho-punk",
        "crust punk",
        "screamo",
    ],
    "Alternative": [
        "alternative",
        "alt",
        "grunge",
        "post-grunge",
        "experimental rock",
        "art rock",
        "krautrock",
    ],
}


def _validate_mapping_against_canonical() -> None:
    """Assert :data:`GENRE_MAPPING` keys cover the canonical label set.

    Drift between the mapping and :mod:`backend.core.genres` would
    silently drop a canonical genre off the For-You page or render a
    chip with no underlying signal — both are easy to miss in code
    review, so we fail loudly at import time instead.

    Raises:
        AssertionError: When a canonical label has no mapping entry, or
            a mapping entry has no canonical label.
    """
    canonical = set(GENRE_LABELS.values())
    mapped = set(GENRE_MAPPING.keys())
    missing = canonical - mapped
    extra = mapped - canonical
    if missing or extra:
        raise AssertionError(
            "GENRE_MAPPING is out of sync with backend.core.genres.GENRE_LABELS. "
            f"Missing canonical labels: {sorted(missing)}; "
            f"unknown labels in mapping: {sorted(extra)}."
        )


_validate_mapping_against_canonical()


def clean_tag(tag: str) -> str:
    """Normalize a raw tag for comparison against canonical patterns.

    Lowercases the input, strips outer whitespace, and converts
    underscores to spaces (Last.fm sometimes returns ``hip_hop`` rather
    than ``hip hop``). Keeps hyphens intact because the mapping
    dictionary distinguishes ``post-punk`` from ``post punk``.

    Args:
        tag: Raw tag string from MusicBrainz or Last.fm.

    Returns:
        Cleaned tag suitable for substring comparison against
        :data:`GENRE_MAPPING` values. Empty string for an empty input.
    """
    return tag.lower().strip().replace("_", " ")


def is_noise_tag(cleaned: str) -> bool:
    """Return True for tags that should never reach the mapper.

    Filters tags that have already been through :func:`clean_tag`.
    Empty or single-character tags are treated as noise unconditionally,
    since Last.fm occasionally returns single-letter tags ("a", "b")
    that would match every canonical via substring containment.

    Args:
        cleaned: Output of :func:`clean_tag`.

    Returns:
        True when the tag should be discarded before mapping, else
        False.
    """
    if len(cleaned) < 2:
        return True
    return cleaned in NOISE_TAG_PATTERNS


def _matches_pattern(cleaned: str, patterns: list[str]) -> bool:
    """Return True when ``cleaned`` contains any pattern in ``patterns``.

    Substring containment, not equality — ``"indie rock/pop"`` matches
    the pattern ``"indie rock"``. Patterns are checked in list order so
    callers control the tiebreak when more than one pattern would fire
    on the same tag.

    Args:
        cleaned: A cleaned tag (output of :func:`clean_tag`).
        patterns: The pattern list for one canonical genre.

    Returns:
        True at the first pattern that appears as a substring of
        ``cleaned``, else False.
    """
    return any(pattern in cleaned for pattern in patterns)


def map_tags_to_canonical(tags: list[str]) -> dict[str, int]:
    """Map a list of raw tag strings to canonical genre hit counts.

    Each tag is cleaned, filtered against :func:`is_noise_tag`, and
    checked against every canonical genre's pattern list. A tag
    matching multiple patterns inside the same canonical counts as one
    hit for that canonical. A tag matching across canonicals counts as
    one hit for each — the merger rebalances later via the source
    weighting.

    Args:
        tags: Raw tag strings from any source.

    Returns:
        Mapping of canonical genre label to hit count. Canonicals with
        zero hits are omitted from the result.
    """
    counts: dict[str, int] = {}
    for raw in tags:
        if not isinstance(raw, str):
            continue
        cleaned = clean_tag(raw)
        if is_noise_tag(cleaned):
            continue
        for canonical, patterns in GENRE_MAPPING.items():
            if _matches_pattern(cleaned, patterns):
                counts[canonical] = counts.get(canonical, 0) + 1
    return counts


def _vote_weight(vote_count: int) -> float:
    """Return the per-tag hit weight implied by a MusicBrainz vote count.

    MusicBrainz attaches a community vote count to every genre/tag.
    Heavily-voted genres carry more confidence, but linear scaling lets
    a single 50-vote tag dwarf five 1-vote tags, which would let
    fringe outliers steamroll the consensus signal. Logarithmic scaling
    (``log2(vote_count + 1)``, floored at 1.0) preserves the rank
    ordering while keeping single high-vote tags from dominating.

    Args:
        vote_count: Raw ``count`` field from a MusicBrainz genre/tag
            entry.

    Returns:
        Weight in 1.0-and-up. Always at least 1.0 so a zero-vote tag
        still contributes something — MusicBrainz includes some tags
        with no votes that are still meaningful.
    """
    if vote_count <= 0:
        return 1.0
    return max(1.0, math.log2(vote_count + 1))


def map_musicbrainz_genres(genres: list[dict[str, Any]]) -> dict[str, float]:
    """Map MusicBrainz genre/tag entries to weighted canonical hit counts.

    MusicBrainz returns genre objects with ``name`` and ``count`` (the
    community vote count). Each entry's hit weight is
    ``max(1, log2(count + 1))`` per :func:`_vote_weight` so a 50-vote
    tag contributes more than a 1-vote tag without dominating the
    merge. Entries are routed into canonical buckets via
    :func:`map_tags_to_canonical` after their per-tag weight is set, so
    a single MusicBrainz entry that maps to two canonicals contributes
    its weight to each.

    Args:
        genres: Raw ``genres`` (or ``tags``) list as stored on
            :attr:`Artist.musicbrainz_genres`. Items are
            ``{"name": str, "count": int}`` dicts; malformed entries
            are skipped.

    Returns:
        Mapping of canonical genre label to weighted hit count.
        Empty dict when ``genres`` is empty or contains no usable
        entries.
    """
    weighted: dict[str, float] = {}
    for entry in genres:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        count_raw = entry.get("count", 0)
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            count = 0
        weight = _vote_weight(count)
        per_canonical = map_tags_to_canonical([name])
        for canonical, hits in per_canonical.items():
            weighted[canonical] = weighted.get(canonical, 0.0) + weight * hits
    return weighted


def map_lastfm_tags(tags: list[dict[str, Any]]) -> dict[str, float]:
    """Map Last.fm tag entries to weighted canonical hit counts.

    Last.fm tag arrays are popularity-ordered; the top entry is the
    most-applied user tag. Tags in the top 5 carry 3x weight, the next
    5 carry 2x, and the long tail counts once — the head of the array
    is the part of the user-generated signal that's actually agreed
    upon, and the tail is dominated by single-user idiosyncrasies.

    Args:
        tags: Raw ``tag`` list as stored on :attr:`Artist.lastfm_tags`.
            Items are ``{"name": str, "url": str}`` dicts in popularity
            order; malformed entries are skipped.

    Returns:
        Mapping of canonical genre label to weighted hit count.
        Empty dict when ``tags`` is empty or contains no usable
        entries.
    """
    weighted: dict[str, float] = {}
    for index, entry in enumerate(tags):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if index < _LASTFM_TOP_BAND:
            weight = _LASTFM_TOP_WEIGHT
        elif index < _LASTFM_MID_BAND:
            weight = _LASTFM_MID_WEIGHT
        else:
            weight = _LASTFM_TAIL_WEIGHT
        per_canonical = map_tags_to_canonical([name])
        for canonical, hits in per_canonical.items():
            weighted[canonical] = weighted.get(canonical, 0.0) + weight * hits
    return weighted


def normalize_to_confidence(raw_scores: dict[str, float]) -> dict[str, float]:
    """Rescale per-canonical raw scores to confidence values in 0.0-1.0.

    The strongest canonical for an artist becomes the reference (1.0)
    and every other score is its ratio against the reference. Confidence
    is therefore "how strong is this genre signal compared to the
    strongest genre signal for this artist," which is comparable across
    artists in a way absolute counts are not — an artist with one
    strong signal and an artist with five weak signals shouldn't both
    report 1.0 confidence on every reported genre.

    Args:
        raw_scores: Output of the merger (sum of weighted source hit
            counts per canonical).

    Returns:
        Mapping of canonical label to confidence in 0.0-1.0. Empty dict
        when ``raw_scores`` is empty or every score is zero.
    """
    if not raw_scores:
        return {}
    max_score = max(raw_scores.values())
    if max_score <= 0:
        return {}
    return {genre: score / max_score for genre, score in raw_scores.items()}


def normalize_genres(
    musicbrainz_genres: list[dict[str, Any]] | None,
    lastfm_tags: list[dict[str, Any]] | None,
) -> tuple[list[str], dict[str, float]]:
    """Combine MusicBrainz + Last.fm signals into canonical genre output.

    The full normalization pipeline:

    1. Map each source independently to its weighted canonical hit
       counts.
    2. Multiply each source's contribution by its source weight from
       :data:`SOURCE_WEIGHTS` and sum across sources.
    3. Rescale to confidence via :func:`normalize_to_confidence`.
    4. Drop canonicals below :data:`MIN_CONFIDENCE_THRESHOLD`.
    5. Return the top :data:`MAX_CANONICAL_GENRES` ordered by
       confidence descending. Ties break alphabetically so the output
       is deterministic across runs.

    Args:
        musicbrainz_genres: Raw MusicBrainz ``genres`` blob (combined
            with the curated list from :attr:`Artist.musicbrainz_genres`
            *and* :attr:`Artist.musicbrainz_tags`; callers should
            concatenate before calling). ``None`` is treated as no data.
        lastfm_tags: Raw Last.fm tag blob from
            :attr:`Artist.lastfm_tags`. ``None`` is treated as no data.

    Returns:
        Tuple ``(genres, confidence)`` where ``genres`` is the ordered
        canonical list and ``confidence`` maps each genre in the list
        to its 0.0-1.0 confidence score. Returns ``([], {})`` when
        neither source produces a canonical hit above the threshold.
    """
    mb_hits = map_musicbrainz_genres(musicbrainz_genres or [])
    lfm_hits = map_lastfm_tags(lastfm_tags or [])

    combined: dict[str, float] = {}
    for canonical, score in mb_hits.items():
        combined[canonical] = (
            combined.get(canonical, 0.0) + score * SOURCE_WEIGHTS["musicbrainz"]
        )
    for canonical, score in lfm_hits.items():
        combined[canonical] = (
            combined.get(canonical, 0.0) + score * SOURCE_WEIGHTS["lastfm"]
        )

    confidence = normalize_to_confidence(combined)
    filtered = {
        genre: score
        for genre, score in confidence.items()
        if score >= MIN_CONFIDENCE_THRESHOLD
    }
    if not filtered:
        return [], {}

    ordered = sorted(filtered.items(), key=lambda item: (-item[1], item[0]))
    top = ordered[:MAX_CANONICAL_GENRES]
    return [genre for genre, _ in top], dict(top)
