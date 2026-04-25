"""Unit tests for :mod:`backend.recommendations.scorers.venue_affinity`.

The scorer is a pure function of (precomputed venue-affinity map, event).
Tests use lightweight dataclass fakes for events; no database access.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from backend.recommendations.scorers.venue_affinity import VenueAffinityScorer


@dataclass
class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        venue_id: UUID of the venue this event belongs to. ``None`` for
            events with no venue (festival promo rows, etc.).
    """

    venue_id: uuid.UUID | None


def test_score_returns_none_when_user_has_no_saved_venues() -> None:
    """An empty affinity map → scorer abstains on every event."""
    scorer = VenueAffinityScorer({})
    event = _FakeEvent(venue_id=uuid.uuid4())
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_returns_none_when_event_has_no_venue() -> None:
    """A venueless event (rare, but possible) gets no score."""
    scorer = VenueAffinityScorer(
        {uuid.uuid4(): {"count": 3, "name": "Black Cat"}},
    )
    event = _FakeEvent(venue_id=None)
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_returns_none_when_venue_not_in_user_history() -> None:
    """An event at a venue the user has never saved gets no score."""
    saved_venue = uuid.uuid4()
    other_venue = uuid.uuid4()
    scorer = VenueAffinityScorer(
        {saved_venue: {"count": 2, "name": "Black Cat"}},
    )
    assert scorer.score(_FakeEvent(venue_id=other_venue)) is None  # type: ignore[arg-type]


def test_score_one_save_returns_base_score() -> None:
    """A single saved show is the floor of the affinity score curve."""
    venue_id = uuid.uuid4()
    scorer = VenueAffinityScorer(
        {venue_id: {"count": 1, "name": "9:30 Club"}},
    )
    result = scorer.score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.2
    assert result["matched_venue_id"] == str(venue_id)
    assert result["matched_venue_name"] == "9:30 Club"
    assert result["saved_count"] == 1


def test_score_increments_with_additional_saves() -> None:
    """Two saves → 0.3, three saves → 0.4 (the saturation point)."""
    venue_id = uuid.uuid4()
    two_saves = VenueAffinityScorer(
        {venue_id: {"count": 2, "name": "DC9"}},
    ).score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    three_saves = VenueAffinityScorer(
        {venue_id: {"count": 3, "name": "DC9"}},
    ).score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    assert two_saves is not None and three_saves is not None
    assert two_saves["score"] == pytest.approx(0.3)
    assert three_saves["score"] == pytest.approx(0.4)


def test_score_saturates_at_max_for_heavy_users() -> None:
    """Many saved shows do not exceed the 0.4 cap.

    Past three visits the venue is a known quantity; additional saves
    add little information about taste so the score plateaus to keep
    one heavily-attended venue from drowning out artist signal.
    """
    venue_id = uuid.uuid4()
    scorer = VenueAffinityScorer(
        {venue_id: {"count": 25, "name": "The Anthem"}},
    )
    result = scorer.score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.4


def test_score_treats_zero_or_negative_count_as_abstain() -> None:
    """Defensive: a row with a non-positive count shouldn't fire."""
    venue_id = uuid.uuid4()
    scorer = VenueAffinityScorer(
        {venue_id: {"count": 0, "name": "Echostage"}},
    )
    assert scorer.score(_FakeEvent(venue_id=venue_id)) is None  # type: ignore[arg-type]


def test_score_handles_missing_venue_name_gracefully() -> None:
    """A venue row that landed without a name still scores (UI handles None)."""
    venue_id = uuid.uuid4()
    scorer = VenueAffinityScorer(
        {venue_id: {"count": 1}},
    )
    result = scorer.score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    assert result is not None
    assert result["matched_venue_name"] is None
