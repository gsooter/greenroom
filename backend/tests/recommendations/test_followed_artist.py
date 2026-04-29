"""Unit tests for :mod:`backend.recommendations.scorers.followed_artist`.

The scorer is a pure function of (precomputed signal payload, event).
Tests use lightweight dataclass fakes for events; no database access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.recommendations.scorers.followed_artist import FollowedArtistScorer


@dataclass
class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`."""

    spotify_artist_ids: list[str] | None = field(default_factory=list)
    artists: list[str] | None = field(default_factory=list)


def test_score_returns_none_when_user_follows_nothing() -> None:
    """An empty signal payload → scorer abstains on every event."""
    scorer = FollowedArtistScorer({})
    event = _FakeEvent(spotify_artist_ids=["spot-1"], artists=["Anyone"])
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_matches_spotify_id() -> None:
    """A Spotify-id hit produces the id-match score and chip name."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {"spot-a": "Phoebe Bridgers"},
            "names": {"phoebe bridgers": "Phoebe Bridgers"},
        }
    )
    event = _FakeEvent(spotify_artist_ids=["spot-a"], artists=["Some Other Name"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.9
    assert result["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "spotify_id"}
    ]


def test_score_matches_artist_name_when_no_spotify_id_present() -> None:
    """Name-match path covers events scraped without Spotify ids."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {},
            "names": {"phoebe bridgers": "Phoebe Bridgers"},
        }
    )
    event = _FakeEvent(spotify_artist_ids=[], artists=["Phoebe Bridgers"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.8
    assert result["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "artist_name"}
    ]


def test_score_prefers_spotify_id_when_both_paths_match() -> None:
    """An id and a name hit on the same artist collapses to one chip."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {"spot-a": "Phoebe Bridgers"},
            "names": {"phoebe bridgers": "Phoebe Bridgers"},
        }
    )
    event = _FakeEvent(
        spotify_artist_ids=["spot-a"],
        artists=["Phoebe Bridgers"],
    )
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    # Only one chip — id match wins, name match dedupes.
    assert result["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "spotify_id"}
    ]
    assert result["score"] == 0.9


def test_score_emits_one_chip_per_matched_artist() -> None:
    """Two distinct followed artists on the same event → two chips."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {"spot-a": "Phoebe Bridgers"},
            "names": {"big thief": "Big Thief"},
        }
    )
    event = _FakeEvent(
        spotify_artist_ids=["spot-a"],
        artists=["Big Thief", "Some Opener"],
    )
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    names = {chip["name"] for chip in result["matched_artists"]}
    assert names == {"Phoebe Bridgers", "Big Thief"}
    assert result["score"] == 0.9  # best of the two tiers


def test_score_returns_none_when_no_overlap() -> None:
    """An event with none of the followed artists abstains."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {"spot-a": "Phoebe Bridgers"},
            "names": {"phoebe bridgers": "Phoebe Bridgers"},
        }
    )
    event = _FakeEvent(
        spotify_artist_ids=["spot-z"],
        artists=["Some Random Band"],
    )
    assert scorer.score(event) is None  # type: ignore[arg-type]


def test_score_normalizes_artist_names() -> None:
    """Diacritics + casing don't break the name-match path."""
    scorer = FollowedArtistScorer(
        {
            "spotify_ids": {},
            "names": {"beyonce": "Beyoncé"},
        }
    )
    event = _FakeEvent(spotify_artist_ids=[], artists=["BEYONCÉ"])
    result = scorer.score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["matched_artists"] == [{"name": "Beyoncé", "match": "artist_name"}]
