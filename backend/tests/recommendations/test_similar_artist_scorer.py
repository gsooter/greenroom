"""Unit tests for :mod:`backend.recommendations.scorers.similar_artist`.

The scorer is pure function over (anchor artists with weights, similar
artist lookup table, event performer list). Tests exercise it with
light dataclass fakes so nothing touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.recommendations.scorers.similar_artist import (
    DIRECT_FOLLOW_WEIGHT,
    MINIMUM_SIMILARITY_SCORE,
    RECENT_LISTEN_WEIGHT,
    TOP_ARTIST_WEIGHT,
    SimilarArtistScorer,
)


@dataclass
class _FakeEvent:
    """Minimal Event stand-in for similar-artist scorer tests."""

    artists: list[str] | None = field(default_factory=list)
    spotify_artist_ids: list[str] | None = field(default_factory=list)


def _payload(name: str, score: float) -> dict[str, Any]:
    """Build the minimum similarity-row payload the scorer reads."""
    return {"similar_artist_name": name, "similarity_score": score}


def test_returns_none_when_user_has_no_anchor_artists() -> None:
    """No anchors → no signal to derive similar artists from → None."""
    scorer = SimilarArtistScorer(anchor_signals={}, similar_by_anchor={})
    event = _FakeEvent(artists=["Lucy Dacus"])
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_returns_none_when_event_has_no_overlap() -> None:
    scorer = SimilarArtistScorer(
        anchor_signals={"phoebe bridgers": ("Phoebe Bridgers", DIRECT_FOLLOW_WEIGHT)},
        similar_by_anchor={
            "phoebe bridgers": [_payload("Lucy Dacus", 0.95)],
        },
    )
    event = _FakeEvent(artists=["Some Random Band"])
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_scores_positive_when_event_artist_is_similar_to_anchor() -> None:
    """Anchor follow → similar artist on event → match payload."""
    scorer = SimilarArtistScorer(
        anchor_signals={"phoebe bridgers": ("Phoebe Bridgers", DIRECT_FOLLOW_WEIGHT)},
        similar_by_anchor={
            "phoebe bridgers": [_payload("Lucy Dacus", 0.95)],
        },
    )
    event = _FakeEvent(artists=["Lucy Dacus"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] > 0
    matched = result["matched_similar_artists"]
    assert matched[0]["name"] == "Lucy Dacus"
    assert matched[0]["anchor_name"] == "Phoebe Bridgers"


def test_followed_artists_weight_higher_than_recently_played() -> None:
    """Same similar artist via different anchors → followed wins."""
    scorer = SimilarArtistScorer(
        anchor_signals={
            "followed anchor": ("Followed Anchor", DIRECT_FOLLOW_WEIGHT),
            "recent anchor": ("Recent Anchor", RECENT_LISTEN_WEIGHT),
        },
        similar_by_anchor={
            "followed anchor": [_payload("Common Similar", 0.9)],
            "recent anchor": [_payload("Common Similar", 0.9)],
        },
    )
    event = _FakeEvent(artists=["Common Similar"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    matched = result["matched_similar_artists"][0]
    # The picked anchor is the followed one, not the recently-played one.
    assert matched["anchor_name"] == "Followed Anchor"
    assert result["score"] == 0.9 * DIRECT_FOLLOW_WEIGHT


def test_filters_out_matches_below_minimum_threshold() -> None:
    """Edges with score < MINIMUM_SIMILARITY_SCORE are ignored."""
    weak_score = MINIMUM_SIMILARITY_SCORE - 0.1
    scorer = SimilarArtistScorer(
        anchor_signals={"phoebe bridgers": ("Phoebe Bridgers", DIRECT_FOLLOW_WEIGHT)},
        similar_by_anchor={
            "phoebe bridgers": [_payload("Weak Match", weak_score)],
        },
    )
    event = _FakeEvent(artists=["Weak Match"])
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_uses_highest_matching_score_when_multiple_anchors_match() -> None:
    """Same similar artist named by two anchors → take the higher product."""
    scorer = SimilarArtistScorer(
        anchor_signals={
            "anchor a": ("Anchor A", TOP_ARTIST_WEIGHT),
            "anchor b": ("Anchor B", DIRECT_FOLLOW_WEIGHT),
        },
        similar_by_anchor={
            "anchor a": [_payload("Common", 0.95)],
            "anchor b": [_payload("Common", 0.6)],
        },
    )
    event = _FakeEvent(artists=["Common"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    # 0.95 * TOP vs 0.6 * DIRECT — pick whichever is larger.
    expected = max(0.95 * TOP_ARTIST_WEIGHT, 0.6 * DIRECT_FOLLOW_WEIGHT)
    assert result["score"] == expected


def test_capping_score_at_one() -> None:
    """A pathological inflated weight cannot push the contribution above 1.0."""
    scorer = SimilarArtistScorer(
        anchor_signals={"a": ("A", 5.0)},  # inflated
        similar_by_anchor={"a": [_payload("Sim", 1.0)]},
    )
    event = _FakeEvent(artists=["Sim"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] <= 1.0


def test_match_artist_lookup_is_case_insensitive() -> None:
    scorer = SimilarArtistScorer(
        anchor_signals={"phoebe bridgers": ("Phoebe Bridgers", DIRECT_FOLLOW_WEIGHT)},
        similar_by_anchor={
            "phoebe bridgers": [_payload("Lucy Dacus", 0.9)],
        },
    )
    event = _FakeEvent(artists=["LUCY DACUS"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None


def test_scorer_name_constant() -> None:
    """The breakdown key is stable so the UI can reference it."""
    scorer = SimilarArtistScorer(anchor_signals={}, similar_by_anchor={})
    assert scorer.name == "similar_artist"


def test_weights_constants_are_ordered_correctly() -> None:
    """Followed > top > recently-played — the spec's hierarchy."""
    assert DIRECT_FOLLOW_WEIGHT > TOP_ARTIST_WEIGHT
    assert TOP_ARTIST_WEIGHT > RECENT_LISTEN_WEIGHT


def test_minimum_similarity_threshold_default() -> None:
    """Threshold defaults to 0.5 per the spec."""
    assert MINIMUM_SIMILARITY_SCORE == 0.5


def test_skips_self_match_when_event_artist_equals_anchor_name() -> None:
    """If the event artist IS the anchor, ArtistMatchScorer handles it.

    Avoids double-counting by skipping similar-artist matches whose
    similar name normalizes to one of the user's anchor names.
    """
    scorer = SimilarArtistScorer(
        anchor_signals={"phoebe bridgers": ("Phoebe Bridgers", DIRECT_FOLLOW_WEIGHT)},
        similar_by_anchor={
            "phoebe bridgers": [_payload("Phoebe Bridgers", 1.0)],
        },
    )
    event = _FakeEvent(artists=["Phoebe Bridgers"])
    assert scorer.score(event) is None  # type: ignore[arg-type]
