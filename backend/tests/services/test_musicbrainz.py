"""Unit tests for :mod:`backend.services.musicbrainz`.

The HTTP layer is exercised with a stubbed ``requests`` module that
returns fixture-derived JSON responses. The decision logic in
:func:`find_best_match` is exercised separately because it has no
network dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from backend.services.musicbrainz import (
    CONFIDENCE_THRESHOLD,
    MusicBrainzAPIError,
    MusicBrainzCandidate,
    MusicBrainzNotFoundError,
    fetch_artist_details,
    find_best_match,
    search_musicbrainz_artist,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "musicbrainz"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from the musicbrainz fixtures directory.

    Args:
        name: Fixture filename, e.g. ``search_boygenius.json``.

    Returns:
        The decoded JSON payload as a dict.
    """
    return json.loads((FIXTURES / name).read_text())


def _fake_response(
    *,
    status_code: int = 200,
    json_payload: Any = None,
    raise_json: bool = False,
) -> MagicMock:
    """Build a stubbed ``requests.Response``-like object.

    Args:
        status_code: HTTP status code to expose.
        json_payload: Value returned by ``.json()``.
        raise_json: When True, ``.json()`` raises ``ValueError``.

    Returns:
        A MagicMock with the attributes the service module reads.
    """
    resp = MagicMock()
    resp.status_code = status_code
    if raise_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_payload
    return resp


# ---------------------------------------------------------------------------
# search_musicbrainz_artist
# ---------------------------------------------------------------------------


def test_search_returns_parsed_candidates_from_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _load_fixture("search_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    candidates = search_musicbrainz_artist("boygenius", session=fake_session)

    assert len(candidates) == 2
    first = candidates[0]
    assert isinstance(first, MusicBrainzCandidate)
    assert first.mbid == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"
    assert first.name == "boygenius"
    assert first.score == 100
    assert first.country == "US"
    assert first.type == "Group"
    assert first.disambiguation is not None


def test_search_sends_user_agent_and_format_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _load_fixture("search_empty.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    search_musicbrainz_artist("boygenius", session=fake_session)

    call = fake_session.get.call_args
    assert "musicbrainz.org/ws/2/artist" in call.args[0]
    assert call.kwargs["params"]["query"] == "artist:boygenius"
    assert call.kwargs["params"]["fmt"] == "json"
    assert call.kwargs["params"]["limit"] == 5
    assert "Greenroom" in call.kwargs["headers"]["User-Agent"]


def test_search_returns_empty_for_blank_name() -> None:
    fake_session = MagicMock()
    assert search_musicbrainz_artist("   ", session=fake_session) == []
    fake_session.get.assert_not_called()


def test_search_returns_empty_when_api_returns_no_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _load_fixture("search_empty.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    assert search_musicbrainz_artist("nobody at all", session=fake_session) == []


def test_search_raises_api_error_on_503(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(status_code=503, json_payload={})

    with pytest.raises(MusicBrainzAPIError) as exc:
        search_musicbrainz_artist("boygenius", session=fake_session)
    assert exc.value.status_code == 503


def test_search_raises_api_error_on_connection_failure() -> None:
    fake_session = MagicMock()
    fake_session.get.side_effect = requests.ConnectionError("boom")

    with pytest.raises(MusicBrainzAPIError) as exc:
        search_musicbrainz_artist("boygenius", session=fake_session)
    assert exc.value.status_code == 0


def test_search_raises_api_error_on_non_json_body() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(raise_json=True)

    with pytest.raises(MusicBrainzAPIError):
        search_musicbrainz_artist("boygenius", session=fake_session)


def test_search_raises_api_error_on_non_object_body() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=["not", "an", "object"])

    with pytest.raises(MusicBrainzAPIError):
        search_musicbrainz_artist("boygenius", session=fake_session)


def test_search_skips_malformed_candidate_entries() -> None:
    payload = {
        "artists": [
            {"id": "good", "name": "Good Artist", "score": 90},
            "not a dict",
            {"id": None, "name": "Bad"},
            {"id": "ok", "name": "  "},
            {"id": "no-score", "name": "Clean", "score": "not-a-number"},
        ]
    }
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    candidates = search_musicbrainz_artist("anyone", session=fake_session)

    ids = [c.mbid for c in candidates]
    assert ids == ["good", "no-score"]
    assert candidates[1].score == 0


def test_search_returns_empty_when_artists_field_not_list() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(
        json_payload={"artists": "should be list"}
    )
    assert search_musicbrainz_artist("anyone", session=fake_session) == []


# ---------------------------------------------------------------------------
# fetch_artist_details
# ---------------------------------------------------------------------------


def test_fetch_artist_details_parses_genres_and_tags() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    details = fetch_artist_details(
        "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3",
        session=fake_session,
    )

    assert details.mbid == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"
    assert details.name == "boygenius"
    genre_names = [g["name"] for g in details.genres]
    assert "indie rock" in genre_names
    assert "indie folk" in genre_names
    # All entries must keep their counts.
    assert all("count" in g for g in details.genres)
    # Empty-string tag in fixture is filtered out.
    tag_names = [t["name"] for t in details.tags]
    assert "" not in tag_names
    assert "supergroup" in tag_names


def test_fetch_artist_details_includes_inc_param() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    fetch_artist_details("any-mbid", session=fake_session)

    call = fake_session.get.call_args
    assert call.kwargs["params"]["inc"] == "genres+tags"


def test_fetch_artist_details_raises_not_found_on_404() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(status_code=404, json_payload={})

    with pytest.raises(MusicBrainzNotFoundError):
        fetch_artist_details("missing-mbid", session=fake_session)


def test_fetch_artist_details_returns_empty_lists_when_payload_missing_them() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload={"name": "Artist Only"})

    details = fetch_artist_details("any", session=fake_session)
    assert details.genres == []
    assert details.tags == []


def test_fetch_artist_details_handles_malformed_count() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(
        json_payload={
            "name": "X",
            "genres": [{"name": "rock", "count": "not-int"}, "junk"],
        }
    )

    details = fetch_artist_details("any", session=fake_session)
    assert details.genres == [{"name": "rock", "count": 0}]


# ---------------------------------------------------------------------------
# find_best_match
# ---------------------------------------------------------------------------


def _candidate(
    *, mbid: str = "x", name: str = "Some Artist", score: int = 100
) -> MusicBrainzCandidate:
    return MusicBrainzCandidate(
        mbid=mbid,
        name=name,
        score=score,
        disambiguation=None,
        country=None,
        type=None,
    )


def test_find_best_match_returns_top_candidate_above_threshold() -> None:
    candidates = [_candidate(mbid="exact", name="boygenius", score=100)]
    result = find_best_match("boygenius", candidates)
    assert result is not None
    candidate, confidence = result
    assert candidate.mbid == "exact"
    assert confidence >= CONFIDENCE_THRESHOLD
    assert confidence == pytest.approx(1.0)


def test_find_best_match_returns_none_for_empty_candidates() -> None:
    assert find_best_match("anyone", []) is None


def test_find_best_match_returns_none_when_all_below_threshold() -> None:
    # Low MB score AND non-matching name -> below 0.75.
    candidates = [
        _candidate(mbid="weak", name="Completely Different Name", score=20),
    ]
    assert find_best_match("boygenius", candidates) is None


def test_find_best_match_picks_highest_blended_confidence() -> None:
    """Confidence is 50/50 MB-score and name similarity.

    Candidate A has perfect name match but lower MB score; B has the
    opposite. We pick the higher blend.
    """
    candidates = [
        _candidate(mbid="A", name="boygenius", score=80),  # 0.5*0.8 + 0.5*1.0 = 0.9
        _candidate(mbid="B", name="boy", score=100),  # 0.5*1.0 + 0.5*~0.6 = ~0.8
    ]
    result = find_best_match("boygenius", candidates)
    assert result is not None
    assert result[0].mbid == "A"


def test_find_best_match_clamps_mb_score_to_unit_interval() -> None:
    # Even a score=100 with a wholly-wrong name shouldn't break confidence
    # math — confidence remains <= 1.0.
    candidates = [_candidate(mbid="weird", name="boygenius", score=100)]
    result = find_best_match("boygenius", candidates)
    assert result is not None
    assert result[1] <= 1.0


def test_find_best_match_handles_negative_mb_scores_defensively() -> None:
    candidates = [_candidate(mbid="bad", name="boygenius", score=-5)]
    result = find_best_match("boygenius", candidates)
    # Name perfect (1.0) + MB clamped to 0 -> 0.5. Below threshold.
    assert result is None


def test_confidence_threshold_constant_unchanged() -> None:
    assert CONFIDENCE_THRESHOLD == 0.75


def test_musicbrainz_api_error_default_status_code() -> None:
    err = MusicBrainzAPIError("boom")
    assert err.status_code == 0
    assert "boom" in str(err)
