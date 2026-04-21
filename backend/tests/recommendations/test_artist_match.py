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
        tidal_top_artists: Cached Tidal top artists.
        apple_top_artists: Cached Apple Music top artists.
        genre_preferences: Slug list from the onboarding taste step,
            validated against :data:`backend.core.genres.GENRE_SLUGS`.
    """

    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None
    tidal_top_artists: list[dict[str, Any]] | None = None
    apple_top_artists: list[dict[str, Any]] | None = None
    genre_preferences: list[str] | None = None


@dataclass
class _FakeEvent:
    """Minimal stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        artists: Performer name list as populated by scrapers.
        spotify_artist_ids: Spotify artist id list when known.
        genres: Genre tags persisted on the event row; drives the
            genre-overlap fallback tier.
    """

    artists: list[str] | None = field(default_factory=list)
    spotify_artist_ids: list[str] | None = field(default_factory=list)
    genres: list[str] | None = field(default_factory=list)


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


def test_score_matches_tidal_artist_by_name() -> None:
    """A name in the Tidal cache alone should still score on name match."""
    user = _FakeUser(
        tidal_top_artists=[{"id": "t-1", "name": "Tidal Only"}],
    )
    event = _FakeEvent(artists=["Tidal Only"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.85


def test_score_matches_apple_music_artist_by_name() -> None:
    """A name in the Apple Music cache alone should score on name match."""
    user = _FakeUser(
        apple_top_artists=[{"id": "a-1", "name": "Apple Only"}],
    )
    event = _FakeEvent(artists=["Apple Only"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.85


def test_score_unions_caches_across_all_providers() -> None:
    """Each provider's cache contributes to the same lookup tables."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "Spot"}],
        tidal_top_artists=[{"name": "Tide"}],
        apple_top_artists=[{"name": "Appl"}],
    )
    event = _FakeEvent(artists=["Tide", "Appl"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    matched_names = {m["name"] for m in result["matched_artists"]}
    assert matched_names == {"Tide", "Appl"}


def test_score_genre_overlap_fallback_when_no_artist_match() -> None:
    """Event genre intersecting a top-artist genre → 0.5 fallback score."""
    user = _FakeUser(
        spotify_top_artists=[
            {"name": "Phoebe Bridgers", "genres": ["indie rock", "indie pop"]},
        ],
    )
    event = _FakeEvent(artists=["Unknown Opener"], genres=["indie rock"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.5
    assert result["matched_artists"] == []
    assert result["matched_genres"] == ["indie rock"]


def test_score_genre_overlap_is_case_insensitive() -> None:
    """Event genre casing doesn't prevent a match against top-artist genres."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "X", "genres": ["Indie Rock"]}],
    )
    event = _FakeEvent(artists=["Nobody"], genres=["INDIE ROCK", "Shoegaze"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.5
    assert result["matched_genres"] == ["INDIE ROCK"]


def test_score_artist_match_outranks_genre_overlap() -> None:
    """When an artist matches, genre overlap is ignored (no downgrade)."""
    user = _FakeUser(
        spotify_top_artists=[
            {"id": "abc", "name": "Phoebe Bridgers", "genres": ["indie rock"]},
        ],
    )
    event = _FakeEvent(spotify_artist_ids=["abc"], genres=["indie rock"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 1.0
    assert "matched_genres" not in result


def test_score_no_genre_overlap_still_returns_none() -> None:
    """No artist + disjoint genres → scorer abstains."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "X", "genres": ["indie rock"]}],
    )
    event = _FakeEvent(artists=["Nobody"], genres=["reggaeton"])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_no_genre_fallback_when_event_has_no_genres() -> None:
    """Empty event.genres → no fallback match."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "X", "genres": ["indie rock"]}],
    )
    event = _FakeEvent(artists=["Nobody"], genres=[])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_genre_fallback_ignores_malformed_genre_entries() -> None:
    """Non-string / whitespace-only entries in event.genres are skipped."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "X", "genres": ["indie rock"]}],
    )
    event = _FakeEvent(
        artists=["Nobody"],
        genres=[None, 42, "  ", "indie rock"],  # type: ignore[list-item]
    )
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["matched_genres"] == ["indie rock"]


def test_score_genre_preference_match_without_music_service() -> None:
    """Onboarding genre picks alone trigger the 0.5 fallback tier."""
    user = _FakeUser(genre_preferences=["indie-rock"])
    event = _FakeEvent(artists=["Unknown Opener"], genres=["Indie Rock"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.5
    assert result["matched_artists"] == []
    assert "matched_genres" not in result
    assert result["matched_preferences"] == [
        {"slug": "indie-rock", "label": "Indie Rock", "event_genre": "Indie Rock"}
    ]


def test_score_genre_preference_substring_alias_matches() -> None:
    """An alias embedded inside a richer event tag still matches."""
    user = _FakeUser(genre_preferences=["punk"])
    event = _FakeEvent(artists=["Nobody"], genres=["post-punk revival"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["matched_preferences"][0]["slug"] == "punk"


def test_score_genre_preference_dedupes_per_slug() -> None:
    """A slug with multiple matching event genres emits one preference hit."""
    user = _FakeUser(genre_preferences=["electronic"])
    event = _FakeEvent(artists=["X"], genres=["house", "techno"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert len(result["matched_preferences"]) == 1
    assert result["matched_preferences"][0]["slug"] == "electronic"


def test_score_genre_preference_and_top_artist_genres_both_surface() -> None:
    """Top-artist genre overlap and preference overlap can coexist."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "X", "genres": ["shoegaze"]}],
        genre_preferences=["indie-rock"],
    )
    event = _FakeEvent(artists=["Nobody"], genres=["shoegaze", "indie rock"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 0.5
    assert result["matched_genres"] == ["shoegaze"]
    preference_slugs = {p["slug"] for p in result["matched_preferences"]}
    assert preference_slugs == {"indie-rock"}


def test_score_unknown_genre_preference_slug_is_ignored() -> None:
    """A stale or unmapped slug can't produce spurious matches."""
    user = _FakeUser(genre_preferences=["not-a-real-slug"])
    event = _FakeEvent(artists=["Nobody"], genres=["indie rock"])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]


def test_score_artist_match_outranks_preference_overlap() -> None:
    """When artists match, preference overlap is suppressed (no downgrade)."""
    user = _FakeUser(
        spotify_top_artists=[{"id": "abc", "name": "Phoebe Bridgers"}],
        genre_preferences=["indie-rock"],
    )
    event = _FakeEvent(spotify_artist_ids=["abc"], genres=["indie rock"])
    result = ArtistMatchScorer(user).score(event)  # type: ignore[arg-type]
    assert result is not None
    assert result["score"] == 1.0
    assert "matched_preferences" not in result


def test_score_preference_without_event_genres_returns_none() -> None:
    """Event with no genre tags can't fall back to preference matching."""
    user = _FakeUser(genre_preferences=["indie-rock"])
    event = _FakeEvent(artists=["Nobody"], genres=[])
    assert ArtistMatchScorer(user).score(event) is None  # type: ignore[arg-type]
