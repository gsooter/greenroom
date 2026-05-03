"""Tests for :mod:`backend.services.tag_consolidation`.

The pure-function pipeline (normalize_tag, is_useful_for_similarity,
extract_artist_tags) is tested directly with raw dicts that mirror the
shape stored on :attr:`Artist.musicbrainz_genres` /
:attr:`Artist.musicbrainz_tags` / :attr:`Artist.lastfm_tags`. The
database-bound functions (``build_global_tag_blocklist`` and
``consolidate_artist_tags``) live in the data-layer test suite where a
real Postgres session is available.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.tag_consolidation import (
    MAX_TAGS_PER_ARTIST,
    extract_artist_tags,
    is_useful_for_similarity,
    normalize_tag,
)

# ---------------------------------------------------------------------------
# normalize_tag
# ---------------------------------------------------------------------------


class TestNormalizeTag:
    """``normalize_tag`` is the entry point for every source string."""

    def test_lowercases_and_strips(self) -> None:
        assert normalize_tag("  Indie Folk  ") == "indie folk"

    def test_replaces_underscores_with_spaces(self) -> None:
        assert normalize_tag("hip_hop") == "hip hop"

    def test_collapses_multiple_internal_spaces(self) -> None:
        assert normalize_tag("post   punk") == "post punk"

    def test_returns_empty_for_too_short_input(self) -> None:
        assert normalize_tag("a") == ""

    def test_returns_empty_for_too_long_input(self) -> None:
        assert normalize_tag("x" * 60) == ""

    def test_returns_empty_for_blank_input(self) -> None:
        assert normalize_tag("   ") == ""

    def test_returns_empty_for_non_string_input(self) -> None:
        assert normalize_tag(None) == ""  # type: ignore[arg-type]

    def test_preserves_hyphens(self) -> None:
        """Hyphens distinguish 'post-punk' from 'post punk' etc."""
        assert normalize_tag("Post-Punk") == "post-punk"


# ---------------------------------------------------------------------------
# is_useful_for_similarity
# ---------------------------------------------------------------------------


class TestIsUsefulForSimilarity:
    """Per-tag filtering rules — runs after :func:`normalize_tag`."""

    def test_accepts_genre_tags(self) -> None:
        assert is_useful_for_similarity("indie folk") is True
        assert is_useful_for_similarity("midwest emo") is True
        assert is_useful_for_similarity("dream pop") is True

    def test_rejects_listening_habit_tags(self) -> None:
        assert is_useful_for_similarity("seen live") is False
        assert is_useful_for_similarity("favorites") is False
        assert is_useful_for_similarity("amazing") is False

    def test_rejects_pure_decade_tags(self) -> None:
        """Decades are useful for genres but not for similarity."""
        assert is_useful_for_similarity("90s") is False
        assert is_useful_for_similarity("2010s") is False

    def test_rejects_pure_year_tags(self) -> None:
        """Years like '1997' or '2024' are pure metadata."""
        assert is_useful_for_similarity("1997") is False
        assert is_useful_for_similarity("2024") is False

    def test_rejects_pure_geographic_tags(self) -> None:
        assert is_useful_for_similarity("british") is False
        assert is_useful_for_similarity("dc") is False
        assert is_useful_for_similarity("japanese") is False

    def test_rejects_too_short_tags(self) -> None:
        assert is_useful_for_similarity("a") is False
        assert is_useful_for_similarity("") is False

    def test_rejects_too_long_tags(self) -> None:
        assert is_useful_for_similarity("x" * 51) is False

    def test_accepts_tags_with_geographic_modifiers(self) -> None:
        """``british indie`` is genre-bearing even though ``british`` alone is not."""
        assert is_useful_for_similarity("british indie") is True


# ---------------------------------------------------------------------------
# extract_artist_tags
# ---------------------------------------------------------------------------


def _mb_entry(name: str, count: int = 1) -> dict[str, Any]:
    """Build a MusicBrainz-shaped genre/tag entry."""
    return {"name": name, "count": count}


def _lfm_entry(name: str) -> dict[str, Any]:
    """Build a Last.fm-shaped tag entry."""
    return {"name": name, "url": f"https://last.fm/tag/{name}"}


class TestExtractArtistTags:
    """End-to-end source merge logic."""

    def test_returns_empty_list_when_all_sources_are_none(self) -> None:
        assert extract_artist_tags(None, None, None) == []

    def test_returns_empty_list_when_all_sources_are_empty(self) -> None:
        assert extract_artist_tags([], [], []) == []

    def test_extracts_from_musicbrainz_genres_only(self) -> None:
        result = extract_artist_tags(
            [_mb_entry("indie folk", 8), _mb_entry("singer-songwriter", 5)],
            None,
            None,
        )
        assert "indie folk" in result
        assert "singer-songwriter" in result

    def test_extracts_from_lastfm_tags_only(self) -> None:
        result = extract_artist_tags(
            None,
            None,
            [_lfm_entry("dream pop"), _lfm_entry("shoegaze")],
        )
        assert result[0] == "dream pop"
        assert result[1] == "shoegaze"

    def test_filters_noise_tags(self) -> None:
        """Listening-habit tags from any source must not survive."""
        result = extract_artist_tags(
            None,
            [_mb_entry("seen live", 30)],
            [_lfm_entry("favorites"), _lfm_entry("indie")],
        )
        assert "seen live" not in result
        assert "favorites" not in result
        assert "indie" in result

    def test_dedupes_tags_appearing_in_multiple_sources(self) -> None:
        """Same tag in MB + Last.fm should appear once, with the summed score."""
        result = extract_artist_tags(
            [_mb_entry("indie rock", 10)],
            [_mb_entry("indie rock", 5)],
            [_lfm_entry("indie rock")],
        )
        assert result.count("indie rock") == 1

    def test_musicbrainz_genres_outrank_lastfm_tail_tags(self) -> None:
        """MB curated genre with a base of 3.0 beats LFM tail at 1.0."""
        # Pad LFM tags so the target is in the tail (index >= 10).
        padding = [_lfm_entry(f"pad{i}") for i in range(11)]
        result = extract_artist_tags(
            [_mb_entry("indie folk", 0)],
            None,
            [*padding, _lfm_entry("dance pop")],
        )
        assert result.index("indie folk") < result.index("dance pop")

    def test_lastfm_top_band_outranks_lastfm_tail(self) -> None:
        """Last.fm position 0 should rank ahead of position >= 10."""
        tags = [_lfm_entry(f"tag{i}") for i in range(15)]
        result = extract_artist_tags(None, None, tags)
        assert result.index("tag0") < result.index("tag14")

    def test_caps_output_at_max_tags_per_artist(self) -> None:
        oversized = [_lfm_entry(f"tag{i}") for i in range(40)]
        result = extract_artist_tags(None, None, oversized)
        assert len(result) <= MAX_TAGS_PER_ARTIST

    def test_dedupes_tag_appearing_with_different_casing(self) -> None:
        result = extract_artist_tags(
            None,
            None,
            [_lfm_entry("Indie Folk"), _lfm_entry("INDIE FOLK")],
        )
        assert result.count("indie folk") == 1

    def test_skips_malformed_entries_silently(self) -> None:
        """Malformed input from either source should never raise."""
        result = extract_artist_tags(
            [{"no_name_field": True}, _mb_entry("indie")],  # type: ignore[list-item]
            None,
            ["not_a_dict", _lfm_entry("folk")],  # type: ignore[list-item]
        )
        assert "indie" in result
        assert "folk" in result

    def test_filters_year_tags(self) -> None:
        result = extract_artist_tags(
            None,
            None,
            [_lfm_entry("1997"), _lfm_entry("indie")],
        )
        assert "1997" not in result
        assert "indie" in result

    def test_returns_deterministic_order_on_score_ties(self) -> None:
        """Score ties break alphabetically so two runs return the same list."""
        # Two MB tags with identical vote counts → identical scores.
        result = extract_artist_tags(
            [_mb_entry("zebra", 5), _mb_entry("alpha", 5)],
            None,
            None,
        )
        assert result.index("alpha") < result.index("zebra")


# ---------------------------------------------------------------------------
# Integration-style: realistic full-source payloads
# ---------------------------------------------------------------------------


class TestRealisticPayloads:
    """End-to-end smoke tests using realistic per-source shapes."""

    def test_phoebe_bridgers_like_payload_yields_meaningful_tags(self) -> None:
        """An indie-folk artist should consolidate to indie/folk-flavored tags."""
        mb_genres = [
            _mb_entry("indie folk", 12),
            _mb_entry("indie rock", 9),
        ]
        mb_tags = [_mb_entry("singer-songwriter", 4)]
        lfm = [
            _lfm_entry("indie"),
            _lfm_entry("indie folk"),
            _lfm_entry("singer-songwriter"),
            _lfm_entry("seen live"),  # noise
            _lfm_entry("favorites"),  # noise
            _lfm_entry("female vocalists"),  # noise
        ]
        result = extract_artist_tags(mb_genres, mb_tags, lfm)
        assert "indie folk" in result[:5]
        assert "singer-songwriter" in result
        assert "seen live" not in result
        assert "favorites" not in result

    def test_dance_artist_payload_yields_electronic_tags(self) -> None:
        mb_genres = [_mb_entry("house", 6), _mb_entry("disco house", 3)]
        lfm = [
            _lfm_entry("house"),
            _lfm_entry("disco"),
            _lfm_entry("dance"),
            _lfm_entry("electronic"),
        ]
        result = extract_artist_tags(mb_genres, None, lfm)
        assert "house" in result
        assert "disco" in result or "dance" in result

    def test_unknown_artist_with_only_one_useful_tag(self) -> None:
        """A new local artist may only have a couple of usable tags."""
        result = extract_artist_tags(
            None,
            None,
            [_lfm_entry("indie rock")],
        )
        assert result == ["indie rock"]


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_constants_are_set_to_documented_values() -> None:
    """Guard against accidental tuning regressions during refactors."""
    from backend.services.tag_consolidation import (
        MAX_DOCUMENT_FREQUENCY,
        MAX_TAGS_PER_ARTIST,
        MIN_GLOBAL_FREQUENCY,
    )

    assert pytest.approx(0.30) == MAX_DOCUMENT_FREQUENCY
    assert MIN_GLOBAL_FREQUENCY == 3
    assert MAX_TAGS_PER_ARTIST == 20
