"""Unit tests for :mod:`backend.recommendations.scorers.followed_venue`.

The scorer is a pure function of (followed-venue map, event). Tests use
lightweight dataclass fakes for events; no database access.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from backend.recommendations.scorers.followed_venue import FollowedVenueScorer


@dataclass
class _FakeEvent:
    """Minimal Event stand-in for the followed-venue scorer."""

    venue_id: uuid.UUID | None


def test_score_returns_none_when_user_follows_no_venues() -> None:
    """Empty followed-venue map → scorer abstains on every event."""
    scorer = FollowedVenueScorer({})
    event = _FakeEvent(venue_id=uuid.uuid4())
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_returns_none_when_event_has_no_venue() -> None:
    """A venueless event (rare, but possible) gets no score."""
    scorer = FollowedVenueScorer({uuid.uuid4(): "Black Cat"})
    event = _FakeEvent(venue_id=None)
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_returns_none_when_venue_not_followed() -> None:
    """An event at an unfollowed venue gets no score."""
    followed = uuid.uuid4()
    other = uuid.uuid4()
    scorer = FollowedVenueScorer({followed: "9:30 Club"})
    assert scorer.score(_FakeEvent(venue_id=other)) is None  # type: ignore[arg-type]


def test_score_emits_followed_venue_chip_payload() -> None:
    """A followed venue produces the chip payload for reasons."""
    venue_id = uuid.uuid4()
    scorer = FollowedVenueScorer({venue_id: "9:30 Club"})
    result = scorer.score(_FakeEvent(venue_id=venue_id))  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.45
    assert result["matched_venue_id"] == str(venue_id)
    assert result["matched_venue_name"] == "9:30 Club"
