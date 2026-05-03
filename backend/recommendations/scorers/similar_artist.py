"""Similar-artist scorer driven by Last.fm's ``artist.getSimilar``.

Slots between the strong tier of :class:`ArtistMatchScorer` (exact id
or name match → 1.0/0.85) and the genre-overlap fallback inside that
same scorer (0.5). The output is a fraction of one of the per-anchor
weights below, scaled by the upstream similarity score:

* A user follows artist A. Last.fm says B is 0.92 similar to A. An
  upcoming event lists B. The scorer contributes
  ``0.92 * DIRECT_FOLLOW_WEIGHT``.

* The same user has B in their recently-played list. The scorer
  fires too, but with a weaker contribution (RECENT_LISTEN_WEIGHT).

* When the same similar artist would match against multiple anchors,
  the scorer takes the largest product so the strongest anchor wins.

Per Decision 007, all weights are tunable: the constants live at the
top of the module and the engine reads them only via this scorer's
output, so future tuning is one edit and a test change.
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

# Drop similarity edges below this threshold before scoring. Matches
# the spec — a 0.5 cutoff filters out the long tail Last.fm returns
# (often loose tag-coincidence rather than real similarity).
MINIMUM_SIMILARITY_SCORE = 0.5

# Cap any single contribution at 1.0 so an inflated weight or a buggy
# upstream score can't dominate the engine total.
_MAX_CONTRIBUTION = 1.0

__all__ = [
    "DIRECT_FOLLOW_WEIGHT",
    "MINIMUM_SIMILARITY_SCORE",
    "RECENT_LISTEN_WEIGHT",
    "SCORER_NAME",
    "TOP_ARTIST_WEIGHT",
    "SimilarArtistScorer",
]


class SimilarArtistScorer:
    """Score events by overlap with the user's anchor-derived similar artists.

    Instantiated per scoring pass with a precomputed lookup map keyed
    by normalized anchor name. The engine builds the map once for all
    candidate events so per-event scoring stays O(event performers).

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(
        self,
        anchor_signals: dict[str, tuple[str, float]],
        similar_by_anchor: dict[str, list[dict[str, Any]]],
        *,
        minimum_score: float = MINIMUM_SIMILARITY_SCORE,
    ) -> None:
        """Bind the per-pass anchor signals and similarity lookup map.

        Args:
            anchor_signals: Mapping from normalized anchor name to a
                ``(display_name, weight)`` tuple. Weight is the per-
                anchor multiplier applied to similarity scores; pass
                :data:`DIRECT_FOLLOW_WEIGHT`, :data:`TOP_ARTIST_WEIGHT`,
                or :data:`RECENT_LISTEN_WEIGHT`. Empty dict means the
                scorer abstains on every event.
            similar_by_anchor: Mapping from normalized anchor name to
                the list of similar-artist payloads pulled from
                ``artist_similarity``. Each payload must carry at least
                ``similar_artist_name`` and ``similarity_score``.
                Anchors with no similarity data simply contribute no
                matches; missing keys are tolerated.
            minimum_score: Drop similarity edges below this threshold
                before scoring. Defaults to
                :data:`MINIMUM_SIMILARITY_SCORE`.
        """
        self._anchor_signals = anchor_signals
        self._minimum_score = minimum_score

        # Flatten ``(normalized_similar_name) → list[(anchor_key,
        # anchor_display, anchor_weight, similarity_score)]`` so the
        # per-event hot path is one lookup per performer.
        self._similar_index: dict[str, list[tuple[str, str, float, float]]] = {}
        for anchor_key, payloads in similar_by_anchor.items():
            anchor = anchor_signals.get(anchor_key)
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
                if similarity < self._minimum_score:
                    continue
                normalized = _normalize(name)
                if not normalized:
                    continue
                self._similar_index.setdefault(normalized, []).append(
                    (anchor_key, anchor_display, anchor_weight, similarity)
                )

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score a single event for the bound user.

        Walks the event's performer list, looks each up in the flattened
        similarity index, and picks the best ``(anchor weight * edge
        score)`` per matched similar artist. Skips event artists that
        already appear among the user's anchor names — those are scored
        by :class:`ArtistMatchScorer` and double-counting them here
        would inflate weak shows.

        Args:
            event: The candidate :class:`Event`.

        Returns:
            ``{score, matched_similar_artists}`` when at least one
            performer matches a similar artist above the threshold,
            else ``None``.
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

            best_for_artist: tuple[str, str, float, float] | None = None
            best_contribution = 0.0
            for anchor_key, anchor_display, anchor_weight, similarity in candidates:
                contribution = min(similarity * anchor_weight, _MAX_CONTRIBUTION)
                if contribution > best_contribution:
                    best_contribution = contribution
                    best_for_artist = (
                        anchor_key,
                        anchor_display,
                        anchor_weight,
                        similarity,
                    )
            if best_for_artist is None:
                continue
            anchor_key, anchor_display, anchor_weight, similarity = best_for_artist
            matched.append(
                {
                    "name": performer,
                    "anchor_key": anchor_key,
                    "anchor_name": anchor_display,
                    "anchor_weight": anchor_weight,
                    "similarity_score": similarity,
                }
            )
            best_total = max(best_total, best_contribution)

        if not matched:
            return None
        return {
            "score": min(best_total, _MAX_CONTRIBUTION),
            "matched_similar_artists": matched,
        }
