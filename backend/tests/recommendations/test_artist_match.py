"""Unit tests for :mod:`backend.recommendations.scorers.artist_match`.

The scorer is a pure function of (user top artists, event performer list).
Tests exercise it with light dataclass fakes in place of SQLAlchemy-backed
User/Event objects so nothing touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.recommendations.scorers.artist_match import (
    ArtistMatchScorer,
    _normalize,
)


@dataclass
class _FakeUser:
    """Minimal stand-in for :class:`backend.data.models.users.User`.

    Attributes:
        spotify_top_artists: List of artist dicts as cached from the
            Spotify API, matching the ORM field the scorer reads.
        spotify_recent_artists: List of artist dicts derived from the
            user's recently-played tracks; scorer reads both lists.
    """

    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None


@dataclass
class _FakeEvent:
    """Minimal stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        artists: Performer name list as populated by scrapers.
        spotify_artist_ids: Spotify artist id list when known.
    """

    artists: list[str] | None = field(default_factory=list)
    spotify_artist_ids: list[str] | None = field(default_factory=list)


def test_normalize_strips_diacritics_and_case() -> None:
    """Accented and upper-case names collapse to the same lookup key."""
    assert _normalize("Beyoncé") == _normalize("BEYONCE")
    assert _normalize("  Café  Tacvba  ") == "cafe tacvba"


def test_normalize_collapses_internal_whitespace() -> None:
    """Multiple internal spaces normalize to a single space."""
    assert _normalize("Arcade   Fire") == "arcade fire"


def test_score_returns_none_when_user_has_no_top_artists() -> None:
    """Empty Spotify cache → nothing to match → None result."""
    user = _FakeUser(spotify_top_artists=None)
    event = _FakeEvent(artists=["Phoebe Bridgers"])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_spotify_id_match_scores_one() -> None:
    """A Spotify artist-id hit is the strongest signal → 1.0."""
    user = _FakeUser(spotify_top_artists=[{"id": "abc123", "name": "Phoebe Bridgers"}])
    event = _FakeEvent(spotify_artist_ids=["abc123"], artists=[])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 1.0
    assert result["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "spotify_id"}
    ]


def test_score_artist_name_match_scores_point_eight_five() -> None:
    """A name-only match is slightly weaker than an id match."""
    user = _FakeUser(spotify_top_artists=[{"id": "xyz", "name": "Phoebe Bridgers"}])
    event = _FakeEvent(spotify_artist_ids=[], artists=["PHOEBE BRIDGERS"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.85
    assert result["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "artist_name"}
    ]


def test_score_prefers_id_and_skips_duplicate_name_match() -> None:
    """When id+name both match the same artist, only the id match is kept."""
    user = _FakeUser(spotify_top_artists=[{"id": "abc", "name": "Phoebe Bridgers"}])
    event = _FakeEvent(spotify_artist_ids=["abc"], artists=["Phoebe Bridgers"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 1.0
    assert len(result["matched_artists"]) == 1
    assert result["matched_artists"][0]["match"] == "spotify_id"


def test_score_returns_none_on_no_overlap() -> None:
    """No id or name intersection → None (scorer abstains)."""
    user = _FakeUser(spotify_top_artists=[{"id": "abc", "name": "Phoebe Bridgers"}])
    event = _FakeEvent(spotify_artist_ids=["other"], artists=["Random Band"])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_ignores_non_dict_entries_in_top_artists() -> None:
    """Malformed rows in the Spotify cache shouldn't raise or match."""
    user = _FakeUser(
        spotify_top_artists=["not-a-dict", {"name": "Real Band"}]  # type: ignore[list-item]
    )
    event = _FakeEvent(artists=["Real Band"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.85


def test_score_ignores_non_string_event_artists() -> None:
    """Scraper rows occasionally emit non-string artists; don't crash."""
    user = _FakeUser(spotify_top_artists=[{"name": "Real Band"}])
    event = _FakeEvent(artists=[None, 42, "Real Band"])  # type: ignore[list-item]
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.85


def test_score_skips_top_artists_missing_name_and_id() -> None:
    """Top-artist entries without a usable name/id contribute nothing."""
    user = _FakeUser(spotify_top_artists=[{"id": "", "name": ""}, {"foo": "bar"}])
    event = _FakeEvent(artists=["Anyone"])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_multiple_matches_keeps_best_and_returns_all() -> None:
    """Both an id and a different name match → both listed, best score wins."""
    user = _FakeUser(
        spotify_top_artists=[
            {"id": "aaa", "name": "Headliner"},
            {"name": "Opener"},
        ]
    )
    event = _FakeEvent(
        spotify_artist_ids=["aaa"],
        artists=["Opener"],
    )
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 1.0
    matches = {m["match"] for m in result["matched_artists"]}
    assert matches == {"spotify_id", "artist_name"}
