"""Unit and integration-style tests for :mod:`backend.services.genre_normalization`.

The pure-function nature of normalization makes mocking unnecessary —
each test passes raw dicts that mirror the shape stored on
:attr:`Artist.musicbrainz_genres`/``musicbrainz_tags``/``lastfm_tags``
and asserts on the canonical output. The integration-style tests at
the bottom exercise the full pipeline with realistic per-source
payloads to catch interaction bugs that pure unit tests miss.
"""

from __future__ import annotations

import itertools
import math

import pytest

from backend.services.genre_normalization import (
    GENRE_MAPPING,
    MAX_CANONICAL_GENRES,
    MIN_CONFIDENCE_THRESHOLD,
    SOURCE_WEIGHTS,
    clean_tag,
    is_noise_tag,
    map_lastfm_tags,
    map_musicbrainz_genres,
    map_tags_to_canonical,
    normalize_genres,
    normalize_to_confidence,
)


class TestCleanTag:
    """``clean_tag`` should produce a comparable lowercased form."""

    def test_lowercases_and_strips(self) -> None:
        """Whitespace and case differences should not affect downstream comparison."""
        assert clean_tag("  Indie Rock  ") == "indie rock"

    def test_normalizes_underscores(self) -> None:
        """Last.fm sometimes emits ``hip_hop``; treat it like ``hip hop``."""
        assert clean_tag("hip_hop") == "hip hop"

    def test_handles_empty(self) -> None:
        """Empty input should return empty without raising."""
        assert clean_tag("") == ""


class TestIsNoiseTag:
    """``is_noise_tag`` should drop listening-habit and meta tags."""

    @pytest.mark.parametrize(
        "tag",
        ["seen live", "favorite", "british", "90s", "music", "lol"],
    )
    def test_known_noise_patterns(self, tag: str) -> None:
        """Tags in the noise list should be filtered."""
        assert is_noise_tag(tag) is True

    def test_short_tags_are_noise(self) -> None:
        """Single-character tags would substring-match every canonical."""
        assert is_noise_tag("a") is True

    @pytest.mark.parametrize(
        "tag",
        ["indie rock", "hip hop", "electronic", "jazz", "post-punk"],
    )
    def test_legitimate_tags_pass(self, tag: str) -> None:
        """Real genre tags must not be filtered."""
        assert is_noise_tag(tag) is False


class TestMapTagsToCanonical:
    """``map_tags_to_canonical`` should produce stable hit counts."""

    def test_single_genre_match(self) -> None:
        """A clean genre tag should produce one hit on its canonical."""
        result = map_tags_to_canonical(["indie rock"])
        assert result == {"Indie Rock": 1}

    def test_underscore_normalization(self) -> None:
        """``hip_hop`` (Last.fm style) must map to Hip Hop."""
        result = map_tags_to_canonical(["hip_hop"])
        assert result == {"Hip Hop": 1}

    def test_unmapped_tag_is_silent(self) -> None:
        """A tag with no canonical mapping should not appear in the result."""
        result = map_tags_to_canonical(["polka", "krautrock"])
        # "krautrock" maps to Alternative; "polka" does not map at all.
        assert result == {"Alternative": 1}

    def test_noise_tag_filtered(self) -> None:
        """Noise tags must never reach the mapper."""
        result = map_tags_to_canonical(["seen live", "british", "90s"])
        assert result == {}

    def test_tag_matching_multiple_canonicals(self) -> None:
        """A tag that spans canonicals counts once for each."""
        # "indie folk" matches both Folk ("indie folk") and Indie Rock ("indie").
        result = map_tags_to_canonical(["indie folk"])
        assert result["Folk"] == 1
        assert result["Indie Rock"] == 1

    def test_repeated_tag_accumulates(self) -> None:
        """The mapper sums, not unions — same tag twice = two hits."""
        result = map_tags_to_canonical(["jazz", "jazz"])
        assert result == {"Jazz": 2}

    def test_skips_non_string_entries(self) -> None:
        """Defensive: malformed inputs should be ignored, not crash."""
        result = map_tags_to_canonical(["indie rock", None, 42])  # type: ignore[list-item]
        assert result == {"Indie Rock": 1}


class TestMapMusicBrainzGenres:
    """MusicBrainz vote counts should produce log-scaled hit weights."""

    def test_single_high_vote_genre(self) -> None:
        """A 50-vote genre should weight as ``log2(51)`` ≈ 5.67."""
        result = map_musicbrainz_genres([{"name": "indie rock", "count": 50}])
        assert "Indie Rock" in result
        assert result["Indie Rock"] == pytest.approx(math.log2(51))

    def test_zero_vote_floors_to_one(self) -> None:
        """A zero-vote genre still contributes the floor weight (1.0)."""
        result = map_musicbrainz_genres([{"name": "jazz", "count": 0}])
        assert result == {"Jazz": 1.0}

    def test_higher_votes_outweigh_lower(self) -> None:
        """Vote count must drive relative weighting."""
        result = map_musicbrainz_genres(
            [
                {"name": "indie rock", "count": 100},
                {"name": "folk", "count": 1},
            ]
        )
        assert result["Indie Rock"] > result["Folk"]

    def test_skips_malformed_entries(self) -> None:
        """Entries without a string ``name`` must be ignored."""
        result = map_musicbrainz_genres(
            [
                {"name": "jazz", "count": 5},
                {"name": "", "count": 5},
                {"count": 5},
                "not a dict",  # type: ignore[list-item]
            ]
        )
        assert "Jazz" in result
        assert len(result) == 1


class TestMapLastFMTags:
    """Last.fm tag bucket weights should put the head of the list ahead."""

    def test_top_tag_weighs_more_than_tail(self) -> None:
        """First-bucket tags must outweigh tail-bucket tags."""
        head_only = map_lastfm_tags([{"name": "indie rock", "url": ""}])
        # Tail position (index 11) — pad with non-mapping tags so we don't
        # accidentally double up on Indie Rock from the padding.
        padded = [{"name": "polka", "url": ""}] * 11
        padded.append({"name": "indie rock", "url": ""})
        tail_only = map_lastfm_tags(padded)
        assert head_only["Indie Rock"] > tail_only["Indie Rock"]

    def test_top_band_weight(self) -> None:
        """The first 5 entries each carry the top weight (3.0)."""
        tags = [{"name": "indie rock", "url": ""}]
        result = map_lastfm_tags(tags)
        assert result == {"Indie Rock": 3.0}

    def test_mid_band_weight(self) -> None:
        """Indices 5-9 carry the mid weight (2.0)."""
        tags = [{"name": "polka", "url": ""}] * 5
        tags.append({"name": "indie rock", "url": ""})
        result = map_lastfm_tags(tags)
        assert result == {"Indie Rock": 2.0}

    def test_tail_band_weight(self) -> None:
        """Index 10+ carries the tail weight (1.0)."""
        tags = [{"name": "polka", "url": ""}] * 10
        tags.append({"name": "indie rock", "url": ""})
        result = map_lastfm_tags(tags)
        assert result == {"Indie Rock": 1.0}

    def test_skips_malformed_entries(self) -> None:
        """Malformed entries are skipped without breaking the index walk."""
        result = map_lastfm_tags(
            [
                {"name": "indie rock", "url": ""},
                None,  # type: ignore[list-item]
                {"url": ""},
            ]
        )
        assert "Indie Rock" in result


class TestNormalizeToConfidence:
    """Confidence values should always rescale relative to the top score."""

    def test_top_score_becomes_one(self) -> None:
        """The strongest canonical for an artist anchors at 1.0."""
        result = normalize_to_confidence({"Indie Rock": 6.0, "Folk": 3.0})
        assert result["Indie Rock"] == 1.0
        assert result["Folk"] == 0.5

    def test_empty_input_returns_empty(self) -> None:
        """No raw scores ⇒ no confidence values."""
        assert normalize_to_confidence({}) == {}

    def test_all_zero_returns_empty(self) -> None:
        """If the strongest canonical has score zero, nothing qualifies."""
        assert normalize_to_confidence({"Jazz": 0.0, "Pop": 0.0}) == {}


class TestNormalizeGenres:
    """Top-level pipeline: source weighting, threshold, ordering, cap."""

    def test_returns_empty_when_both_sources_none(self) -> None:
        """No data on either side means no canonical assignment."""
        assert normalize_genres(None, None) == ([], {})

    def test_returns_empty_when_both_sources_empty(self) -> None:
        """Empty lists behave the same as None."""
        assert normalize_genres([], []) == ([], {})

    def test_sources_agree_produces_ranked_output(self) -> None:
        """When both sources point at the same canonical it ranks first."""
        mb = [{"name": "indie rock", "count": 5}]
        lfm = [{"name": "indie rock", "url": ""}]
        genres, confidence = normalize_genres(mb, lfm)
        assert genres == ["Indie Rock"]
        assert confidence["Indie Rock"] == 1.0

    def test_musicbrainz_outweighs_lastfm(self) -> None:
        """Curated MusicBrainz signal dominates the user-tag noise."""
        mb = [{"name": "jazz", "count": 50}]
        lfm = [{"name": "pop", "url": ""}]
        genres, confidence = normalize_genres(mb, lfm)
        # MusicBrainz vote 50 weighted 1.5x ≈ 8.5; Last.fm head pop 3.0
        # weighted 1.0x = 3.0. Jazz must lead.
        assert genres[0] == "Jazz"
        assert confidence["Jazz"] == 1.0
        assert confidence.get("Pop", 0.0) < 1.0

    def test_below_threshold_filtered(self) -> None:
        """Canonicals below 0.5 confidence are dropped from the output."""
        mb = [{"name": "indie rock", "count": 100}]
        # Adds a weak Pop signal that will scale to < 0.5 against the
        # MusicBrainz Indie Rock anchor.
        lfm = [{"name": "polka", "url": ""}] * 11 + [
            {"name": "pop", "url": ""},
        ]
        genres, confidence = normalize_genres(mb, lfm)
        assert "Pop" not in genres
        # Anchor still present.
        assert "Indie Rock" in genres
        for value in confidence.values():
            assert value >= MIN_CONFIDENCE_THRESHOLD

    def test_caps_at_max_results(self) -> None:
        """Output never exceeds :data:`MAX_CANONICAL_GENRES`."""
        # Push many balanced signals through both sources so several
        # canonicals clear the threshold.
        mb = [
            {"name": "indie rock", "count": 30},
            {"name": "alternative", "count": 30},
            {"name": "folk", "count": 30},
            {"name": "punk", "count": 30},
            {"name": "pop", "count": 30},
            {"name": "jazz", "count": 30},
            {"name": "metal", "count": 30},
        ]
        lfm = [
            {"name": "indie rock", "url": ""},
            {"name": "alternative", "url": ""},
            {"name": "folk", "url": ""},
            {"name": "punk", "url": ""},
            {"name": "pop", "url": ""},
            {"name": "jazz", "url": ""},
            {"name": "metal", "url": ""},
        ]
        genres, confidence = normalize_genres(mb, lfm)
        assert len(genres) <= MAX_CANONICAL_GENRES
        assert len(confidence) <= MAX_CANONICAL_GENRES

    def test_ranking_descends_by_confidence(self) -> None:
        """The output list mirrors the confidence ordering."""
        mb = [
            {"name": "indie rock", "count": 30},
            {"name": "folk", "count": 5},
        ]
        genres, confidence = normalize_genres(mb, None)
        for first, second in itertools.pairwise(genres):
            assert confidence[first] >= confidence[second]


class TestSourceConfiguration:
    """Light guard tests so the module's contract stays self-consistent."""

    def test_canonical_labels_match_genres_module(self) -> None:
        """Every key in :data:`GENRE_MAPPING` exists in the canonical labels."""
        # If this fails, _validate_mapping_against_canonical at import time
        # already raised — but the explicit test pins the contract.
        from backend.core.genres import GENRE_LABELS

        canonical = set(GENRE_LABELS.values())
        assert set(GENRE_MAPPING.keys()) == canonical

    def test_musicbrainz_weight_dominates_lastfm(self) -> None:
        """The 1.5x curated bonus is intentional, not a mistake."""
        assert SOURCE_WEIGHTS["musicbrainz"] > SOURCE_WEIGHTS["lastfm"]


# ---------------------------------------------------------------------------
# Integration-style tests with realistic per-source payload shapes.
# ---------------------------------------------------------------------------


def test_integration_indie_rock_artist_from_both_sources() -> None:
    """Boygenius-shaped payload: heavy indie-rock + folk on both sides."""
    mb = [
        {"name": "indie rock", "count": 8},
        {"name": "folk", "count": 3},
        {"name": "alternative rock", "count": 4},
    ]
    lfm = [
        {"name": "indie rock", "url": ""},
        {"name": "indie folk", "url": ""},
        {"name": "singer-songwriter", "url": ""},
        {"name": "seen live", "url": ""},
        {"name": "female vocalists", "url": ""},
        {"name": "alternative", "url": ""},
    ]
    genres, confidence = normalize_genres(mb, lfm)
    assert genres[0] == "Indie Rock"
    assert "Folk" in genres
    # Noise tags ("seen live", "female vocalists") never make it through.
    # Folk ranks below Indie Rock.
    assert confidence["Indie Rock"] >= confidence["Folk"]


def test_integration_electronic_artist() -> None:
    """Honey Dijon-shaped payload: deep house signal on both sides."""
    mb = [
        {"name": "house", "count": 5},
        {"name": "electronic", "count": 4},
        {"name": "deep house", "count": 3},
    ]
    lfm = [
        {"name": "house", "url": ""},
        {"name": "techno", "url": ""},
        {"name": "electronic", "url": ""},
        {"name": "dance", "url": ""},
        {"name": "chicago house", "url": ""},
    ]
    genres, confidence = normalize_genres(mb, lfm)
    assert genres == ["Electronic"]
    assert confidence["Electronic"] == 1.0


def test_integration_hip_hop_artist() -> None:
    """A Tribe Called Quest-shaped payload."""
    mb = [
        {"name": "hip hop", "count": 9},
        {"name": "conscious hip hop", "count": 4},
        {"name": "jazz rap", "count": 2},
    ]
    lfm = [
        {"name": "hip-hop", "url": ""},
        {"name": "rap", "url": ""},
        {"name": "conscious hip hop", "url": ""},
        {"name": "jazz rap", "url": ""},
        {"name": "alternative hip hop", "url": ""},
    ]
    genres, confidence = normalize_genres(mb, lfm)
    assert genres[0] == "Hip Hop"
    assert confidence["Hip Hop"] == 1.0


def test_integration_jazz_artist() -> None:
    """Strictly jazz; no other canonical should clear threshold."""
    mb = [
        {"name": "jazz", "count": 10},
        {"name": "bebop", "count": 5},
        {"name": "hard bop", "count": 3},
    ]
    lfm = [
        {"name": "jazz", "url": ""},
        {"name": "bebop", "url": ""},
        {"name": "hard bop", "url": ""},
        {"name": "instrumental", "url": ""},
        {"name": "saxophone", "url": ""},
    ]
    genres, confidence = normalize_genres(mb, lfm)
    assert genres == ["Jazz"]
    assert confidence["Jazz"] == 1.0


def test_integration_disagreement_resolved_by_musicbrainz() -> None:
    """Sources disagree: MusicBrainz curated genre wins.

    Validates the source-weighting logic. MusicBrainz says folk; Last.fm
    crowd has slapped lots of pop tags (a common Last.fm noise pattern).
    The curated edge should keep folk ahead.
    """
    mb = [
        {"name": "folk", "count": 12},
        {"name": "americana", "count": 8},
        {"name": "indie folk", "count": 5},
    ]
    lfm = [
        {"name": "pop", "url": ""},
        {"name": "indie pop", "url": ""},
        {"name": "indie", "url": ""},
        {"name": "female vocalists", "url": ""},
        {"name": "favorite", "url": ""},
    ]
    genres, confidence = normalize_genres(mb, lfm)
    assert genres[0] == "Folk"
    assert confidence["Folk"] == 1.0
    # Pop may still appear if it clears the threshold, but never first.
    if "Pop" in genres:
        assert confidence["Pop"] < confidence["Folk"]
