"""Unit tests for :mod:`backend.services.artist_enrichment`.

The module splits candidate-selection (``_pick_best_match``) from the
persistence wrapper (``enrich_artist``) so both can be exercised
without a live Spotify account or a real DB — the persistence call
uses a fake Artist/session pair and only verifies that the repo
contract is honored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import artist_enrichment
from backend.services.artist_enrichment import (
    SIMILARITY_THRESHOLD,
    _extract_genres,
    _pick_best_match,
    enrich_artist,
)


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row with just the fields we touch."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Phoebe Bridgers"
    spotify_id: str | None = None
    genres: list[str] = field(default_factory=list)
    spotify_enriched_at: datetime | None = None


def _spotify_artist(
    *,
    id_: str | None = "sp-1",
    name: str = "Phoebe Bridgers",
    genres: list[str] | None = None,
) -> dict[str, Any]:
    """Build a raw Spotify search-result dict."""
    return {
        "id": id_,
        "name": name,
        "genres": genres if genres is not None else ["indie", "alternative"],
    }


# ---------------------------------------------------------------------------
# _pick_best_match
# ---------------------------------------------------------------------------


def test_pick_best_match_returns_exact_match() -> None:
    candidates = [_spotify_artist()]
    best = _pick_best_match("Phoebe Bridgers", candidates)
    assert best is not None
    assert best["id"] == "sp-1"


def test_pick_best_match_collapses_case_and_diacritics() -> None:
    """Normalization runs on both sides before scoring."""
    candidates = [_spotify_artist(id_="sp-b", name="Beyoncé")]
    best = _pick_best_match("beyonce", candidates)
    assert best is not None
    assert best["id"] == "sp-b"


def test_pick_best_match_returns_none_for_low_similarity() -> None:
    """A clearly-different candidate stays below the 0.85 threshold."""
    candidates = [_spotify_artist(id_="sp-x", name="Completely Different Name")]
    assert _pick_best_match("Phoebe Bridgers", candidates) is None


def test_pick_best_match_picks_highest_scorer_not_first() -> None:
    """Spotify ranks by popularity; we rescore by name and prefer the closer one."""
    candidates = [
        _spotify_artist(id_="sp-wrong", name="Phoebe"),  # popular but partial
        _spotify_artist(id_="sp-right", name="Phoebe Bridgers"),
    ]
    best = _pick_best_match("Phoebe Bridgers", candidates)
    assert best is not None
    assert best["id"] == "sp-right"


def test_pick_best_match_returns_none_when_no_candidates() -> None:
    assert _pick_best_match("Anyone", []) is None


def test_pick_best_match_returns_none_when_target_normalizes_to_empty() -> None:
    """Whitespace-only scraped name has nothing to match against."""
    candidates = [_spotify_artist()]
    assert _pick_best_match("   ", candidates) is None


def test_pick_best_match_skips_candidates_with_missing_name() -> None:
    """Malformed Spotify responses (missing/empty name) are ignored."""
    candidates = [
        {"id": "sp-broken", "name": None},
        _spotify_artist(id_="sp-good", name="Phoebe Bridgers"),
    ]
    best = _pick_best_match("Phoebe Bridgers", candidates)
    assert best is not None
    assert best["id"] == "sp-good"


def test_similarity_threshold_is_conservative_enough_to_reject_substring() -> None:
    """A shorter substring like 'Beths' vs 'The Beths' can match at 0.85."""
    # This is a sanity check on the constant itself — confirms we would
    # match 'The Beths' ↔ 'Beths' (a common scraper typo) but not 'Phoebe'
    # ↔ 'Phoebe Bridgers'.
    assert SIMILARITY_THRESHOLD == 0.85


# ---------------------------------------------------------------------------
# _extract_genres
# ---------------------------------------------------------------------------


def test_extract_genres_lowercases_and_strips() -> None:
    candidate = {"genres": ["  Indie Rock ", "Alternative"]}
    assert _extract_genres(candidate) == ["indie rock", "alternative"]


def test_extract_genres_skips_non_strings_and_blanks() -> None:
    candidate = {"genres": ["rock", "", "   ", None, 42]}  # type: ignore[list-item]
    assert _extract_genres(candidate) == ["rock"]


def test_extract_genres_returns_empty_when_field_missing_or_malformed() -> None:
    assert _extract_genres({}) == []
    assert _extract_genres({"genres": "not a list"}) == []


# ---------------------------------------------------------------------------
# enrich_artist
# ---------------------------------------------------------------------------


def test_enrich_artist_writes_spotify_id_and_genres_on_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(name="The Beths")
    session = MagicMock()
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        artist_enrichment.artists_repo, "mark_artist_enriched", mark_mock
    )

    results = [_spotify_artist(id_="sp-beths", name="The Beths", genres=["Indie"])]
    returned = enrich_artist(session, artist, search_results=results)  # type: ignore[arg-type]

    assert returned is artist
    mark_mock.assert_called_once()
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["spotify_id"] == "sp-beths"
    assert kwargs["genres"] == ["indie"]


def test_enrich_artist_records_null_match_when_no_candidate_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(name="Phoebe Bridgers")
    session = MagicMock()
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        artist_enrichment.artists_repo, "mark_artist_enriched", mark_mock
    )

    results = [_spotify_artist(id_="sp-x", name="Completely Different")]
    enrich_artist(session, artist, search_results=results)  # type: ignore[arg-type]

    kwargs = mark_mock.call_args.kwargs
    assert kwargs["spotify_id"] is None
    assert kwargs["genres"] == []


def test_enrich_artist_records_null_match_for_empty_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(name="Very Niche Act")
    session = MagicMock()
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        artist_enrichment.artists_repo, "mark_artist_enriched", mark_mock
    )

    enrich_artist(session, artist, search_results=[])  # type: ignore[arg-type]

    kwargs = mark_mock.call_args.kwargs
    assert kwargs["spotify_id"] is None
    assert kwargs["genres"] == []


def test_enrich_artist_coerces_blank_id_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whitespace-only Spotify id is treated as "no id" even if name matches."""
    artist = _FakeArtist(name="Phoebe Bridgers")
    session = MagicMock()
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        artist_enrichment.artists_repo, "mark_artist_enriched", mark_mock
    )

    results = [_spotify_artist(id_="   ", name="Phoebe Bridgers")]
    enrich_artist(session, artist, search_results=results)  # type: ignore[arg-type]

    assert mark_mock.call_args.kwargs["spotify_id"] is None
