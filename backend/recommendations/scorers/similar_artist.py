"""Similar-artist scorer driven by Last.fm + tag-overlap signals.

Slots between the strong tier of :class:`ArtistMatchScorer` (exact id
or name match → 1.0/0.85) and the genre-overlap fallback inside that
same scorer (0.5). Two complementary similarity signals contribute:

* **Last.fm collaborative similarity** (Decision 059). The primary
  signal — Last.fm's ``artist.getSimilar`` returns a curated, weighted
  list of artists that share listeners with the anchor. The output is
  the per-anchor weight times the upstream similarity score.

* **Tag-overlap similarity** (Decision 060). The fallback signal —
  computed at recommendation time from the deduplicated, document-
  frequency-filtered :attr:`Artist.granular_tags` projection. Last.fm
  has thin coverage on local and emerging artists; tag overlap fills
  that gap. The output is the per-anchor weight times the Jaccard
  score, then scaled down by :data:`TAG_SIMILARITY_WEIGHT` so a tag
  match never out-scores a comparable Last.fm match.

When the same similar artist would match against multiple anchors or
both signals, the scorer takes the largest product so the strongest
signal wins. Per Decision 007 all weights are tunable: the constants
live at the top of the module and the engine reads them only via
this scorer's output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.text import normalize_artist_name as _normalize

if TYPE_CHECKING:
    from backend.data.models.events import Event

SCORER_NAME = "similar_artist"

# Scorer weights — per-anchor strength multipliers applied to the
# upstream similarity score. Order matters: a directly followed artist
# is a stronger taste signal than a top-played artist, which is in
# turn stronger than a recently-played artist. See the module docstring
# for the rationale and Decision 007 for the broader philosophy.
DIRECT_FOLLOW_WEIGHT = 0.7
TOP_ARTIST_WEIGHT = 0.6
RECENT_LISTEN_WEIGHT = 0.4

# Drop Last.fm similarity edges below this threshold before scoring.
# Matches the spec — a 0.5 cutoff filters out the long tail Last.fm
# returns (often loose tag-coincidence rather than real similarity).
MINIMUM_SIMILARITY_SCORE = 0.5

# Tag-overlap similarity is real signal but noisier than Last.fm
# collaborative filtering, so we discount it. A tag match must be
# substantially stronger than a Last.fm match to outscore it.
TAG_SIMILARITY_WEIGHT = 0.6

# Drop tag-overlap edges below this Jaccard threshold. Below 0.15
# (e.g. 3 shared tags out of 20+ total) the overlap is a weak signal
# even after the per-tag filtering — well-tuned recommendations should
# fall back to genre-overlap rather than chase noisy similarity.
MINIMUM_TAG_JACCARD = 0.15

# Cap any single contribution at 1.0 so an inflated weight or a buggy
# upstream score can't dominate the engine total.
_MAX_CONTRIBUTION = 1.0

# Match-kind labels — exposed in the breakdown so the UI / frontend
# can differentiate "Similar to X" from "Shares tags with X".
MATCH_KIND_LASTFM = "lastfm"
MATCH_KIND_TAG_OVERLAP = "tag_overlap"

__all__ = [
    "DIRECT_FOLLOW_WEIGHT",
    "MATCH_KIND_LASTFM",
    "MATCH_KIND_TAG_OVERLAP",
    "MINIMUM_SIMILARITY_SCORE",
    "MINIMUM_TAG_JACCARD",
    "RECENT_LISTEN_WEIGHT",
    "SCORER_NAME",
    "TAG_SIMILARITY_WEIGHT",
    "TOP_ARTIST_WEIGHT",
    "SimilarArtistScorer",
]


class SimilarArtistScorer:
    """Score events by overlap with the user's anchor-derived similar artists.

    Instantiated per scoring pass with precomputed lookup maps keyed
    by normalized anchor name. The engine builds the maps once for
    all candidate events so per-event scoring stays O(event
    performers).

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(
        self,
        anchor_signals: dict[str, tuple[str, float]],
        similar_by_anchor: dict[str, list[dict[str, Any]]],
        *,
        tag_similar_by_anchor: dict[str, list[dict[str, Any]]] | None = None,
        minimum_score: float = MINIMUM_SIMILARITY_SCORE,
        minimum_tag_jaccard: float = MINIMUM_TAG_JACCARD,
        tag_similarity_weight: float = TAG_SIMILARITY_WEIGHT,
    ) -> None:
        """Bind the per-pass anchor signals and similarity lookup maps.

        Args:
            anchor_signals: Mapping from normalized anchor name to a
                ``(display_name, weight)`` tuple. Weight is the per-
                anchor multiplier applied to similarity scores; pass
                :data:`DIRECT_FOLLOW_WEIGHT`, :data:`TOP_ARTIST_WEIGHT`,
                or :data:`RECENT_LISTEN_WEIGHT`. Empty dict means the
                scorer abstains on every event.
            similar_by_anchor: Mapping from normalized anchor name to
                Last.fm collaborative similarity payloads pulled from
                ``artist_similarity``. Each payload must carry at
                least ``similar_artist_name`` and ``similarity_score``.
                Anchors with no similarity data simply contribute no
                matches; missing keys are tolerated.
            tag_similar_by_anchor: Mapping from normalized anchor name
                to tag-overlap similarity payloads. Each payload
                carries ``similar_artist_name`` and ``similarity_score``
                where the score is a Jaccard ratio in 0.0-1.0. ``None``
                or empty disables the tag-overlap signal entirely
                (used in tests and when no source artist has any
                granular tags).
            minimum_score: Drop Last.fm edges below this threshold
                before scoring. Defaults to
                :data:`MINIMUM_SIMILARITY_SCORE`.
            minimum_tag_jaccard: Drop tag-overlap edges below this
                Jaccard threshold. Defaults to
                :data:`MINIMUM_TAG_JACCARD`.
            tag_similarity_weight: Discount factor applied to every
                tag-overlap contribution. Defaults to
                :data:`TAG_SIMILARITY_WEIGHT`.
        """
        self._anchor_signals = anchor_signals
        self._minimum_score = minimum_score
        self._minimum_tag_jaccard = minimum_tag_jaccard
        self._tag_similarity_weight = tag_similarity_weight

        # Flatten both signal sources into a single ``(normalized_name)
        # → list[entry]`` index where each entry carries its kind and
        # the contribution multiplier already applied. The hot path is
        # one lookup per event performer.
        self._similar_index: dict[str, list[dict[str, Any]]] = {}
        self._populate_index(
            similar_by_anchor,
            kind=MATCH_KIND_LASTFM,
            minimum=self._minimum_score,
            kind_multiplier=1.0,
        )
        self._populate_index(
            tag_similar_by_anchor or {},
            kind=MATCH_KIND_TAG_OVERLAP,
            minimum=self._minimum_tag_jaccard,
            kind_multiplier=self._tag_similarity_weight,
        )

    def _populate_index(
        self,
        by_anchor: dict[str, list[dict[str, Any]]],
        *,
        kind: str,
        minimum: float,
        kind_multiplier: float,
    ) -> None:
        """Flatten one signal source into the per-performer lookup index.

        Args:
            by_anchor: Per-anchor list of similarity payloads.
            kind: Match-kind label written into each entry.
            minimum: Drop edges with similarity score below this.
            kind_multiplier: Discount applied to every contribution
                from this signal (1.0 for Last.fm, ``TAG_SIMILARITY_
                WEIGHT`` for tag-overlap).
        """
        for anchor_key, payloads in by_anchor.items():
            anchor = self._anchor_signals.get(anchor_key)
            if anchor is None:
                continue
            anchor_display, anchor_weight = anchor
            for payload in payloads or []:
                name = payload.get("similar_artist_name")
                score = payload.get("similarity_score")
                if not isinstance(name, str) or not name.strip():
                    continue
                try:
                    similarity = float(score)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if similarity < minimum:
                    continue
                normalized = _normalize(name)
                if not normalized:
                    continue
                self._similar_index.setdefault(normalized, []).append(
                    {
                        "anchor_key": anchor_key,
                        "anchor_display": anchor_display,
                        "anchor_weight": anchor_weight,
                        "similarity": similarity,
                        "kind": kind,
                        "kind_multiplier": kind_multiplier,
                    }
                )

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score a single event for the bound user.

        Walks the event's performer list, looks each up in the flattened
        similarity index, and picks the best
        ``similarity * anchor_weight * kind_multiplier`` per matched
        similar artist. Skips event artists that already appear among
        the user's anchor names — those are scored by
        :class:`ArtistMatchScorer` and double-counting them here would
        inflate weak shows.

        Args:
            event: The candidate :class:`Event`.

        Returns:
            ``{score, matched_similar_artists}`` when at least one
            performer matches a similar artist above the relevant
            threshold, else ``None``.
        """
        if not self._anchor_signals or not self._similar_index:
            return None

        seen: set[str] = set()
        matched: list[dict[str, Any]] = []
        best_total = 0.0

        for performer in event.artists or []:
            if not isinstance(performer, str):
                continue
            normalized = _normalize(performer)
            if not normalized or normalized in seen:
                continue
            # Avoid double-scoring: ArtistMatchScorer already handles
            # direct hits against the user's anchor set.
            if normalized in self._anchor_signals:
                continue
            candidates = self._similar_index.get(normalized)
            if not candidates:
                continue
            seen.add(normalized)

            best_entry: dict[str, Any] | None = None
            best_contribution = 0.0
            for entry in candidates:
                contribution = min(
                    entry["similarity"]
                    * entry["anchor_weight"]
                    * entry["kind_multiplier"],
                    _MAX_CONTRIBUTION,
                )
                if contribution > best_contribution:
                    best_contribution = contribution
                    best_entry = entry
            if best_entry is None:
                continue
            matched.append(
                {
                    "name": performer,
                    "anchor_key": best_entry["anchor_key"],
                    "anchor_name": best_entry["anchor_display"],
                    "anchor_weight": best_entry["anchor_weight"],
                    "similarity_score": best_entry["similarity"],
                    "match_kind": best_entry["kind"],
                }
            )
            best_total = max(best_total, best_contribution)

        if not matched:
            return None
        return {
            "score": min(best_total, _MAX_CONTRIBUTION),
            "matched_similar_artists": matched,
        }
