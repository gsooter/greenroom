"""Unit tests for :mod:`backend.services.lastfm`.

The HTTP layer is exercised with a stubbed ``requests`` session that
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

from backend.services.lastfm import (
    CONFIDENCE_THRESHOLD,
    LastFMAPIError,
    LastFMCandidate,
    fetch_artist_info_by_mbid,
    fetch_artist_info_by_name,
    find_best_match,
    search_lastfm_artist,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lastfm"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from the lastfm fixtures directory.

    Args:
        name: Fixture filename, e.g. ``search_boygenius.json``.

    Returns:
        The decoded JSON payload as a dict.
    """
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return payload


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


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide an API key so the service module won't short-circuit."""
    from backend.services import lastfm as lastfm_module

    monkeypatch.setattr(lastfm_module, "_get_api_key", lambda: "test-key")


# ---------------------------------------------------------------------------
# search_lastfm_artist
# ---------------------------------------------------------------------------


def test_search_returns_parsed_candidates_from_fixture() -> None:
    payload = _load_fixture("search_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    candidates = search_lastfm_artist("boygenius", session=fake_session)

    assert len(candidates) == 2
    first = candidates[0]
    assert isinstance(first, LastFMCandidate)
    assert first.name == "boygenius"
    assert first.mbid == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"
    assert first.listener_count == 523412
    assert first.url.endswith("/boygenius")


def test_search_sends_user_agent_and_format_params() -> None:
    payload = _load_fixture("search_empty.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    search_lastfm_artist("boygenius", session=fake_session)

    call = fake_session.get.call_args
    assert "ws.audioscrobbler.com/2.0" in call.args[0]
    assert call.kwargs["params"]["method"] == "artist.search"
    assert call.kwargs["params"]["artist"] == "boygenius"
    assert call.kwargs["params"]["format"] == "json"
    assert call.kwargs["params"]["limit"] == 5
    assert call.kwargs["params"]["api_key"] == "test-key"
    assert "Greenroom" in call.kwargs["headers"]["User-Agent"]


def test_search_returns_empty_for_blank_name() -> None:
    fake_session = MagicMock()
    assert search_lastfm_artist("   ", session=fake_session) == []
    fake_session.get.assert_not_called()


def test_search_returns_empty_when_api_returns_no_results() -> None:
    payload = _load_fixture("search_empty.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    assert search_lastfm_artist("nobody at all", session=fake_session) == []


def test_search_raises_api_error_on_503() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(status_code=503, json_payload={})

    with pytest.raises(LastFMAPIError) as exc:
        search_lastfm_artist("boygenius", session=fake_session)
    assert exc.value.status_code == 503


def test_search_raises_api_error_on_connection_failure() -> None:
    fake_session = MagicMock()
    fake_session.get.side_effect = requests.ConnectionError("boom")

    with pytest.raises(LastFMAPIError) as exc:
        search_lastfm_artist("boygenius", session=fake_session)
    assert exc.value.status_code == 0


def test_search_handles_single_artist_dict_not_list() -> None:
    """Last.fm collapses a 1-result list to a single dict in some
    payloads. The parser should treat that as a one-item list rather
    than choke on the type.
    """
    payload = {
        "results": {
            "artistmatches": {
                "artist": {
                    "name": "Solo Match",
                    "listeners": "42",
                    "mbid": "",
                    "url": "https://www.last.fm/music/Solo+Match",
                }
            }
        }
    }
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    candidates = search_lastfm_artist("solo", session=fake_session)
    assert len(candidates) == 1
    assert candidates[0].name == "Solo Match"
    assert candidates[0].listener_count == 42
    assert candidates[0].mbid is None  # empty string normalized to None


def test_search_skips_malformed_candidate_entries() -> None:
    payload = {
        "results": {
            "artistmatches": {
                "artist": [
                    {
                        "name": "Good",
                        "listeners": "100",
                        "url": "u",
                        "mbid": "abc",
                    },
                    "not a dict",
                    {"name": None, "listeners": "1"},
                    {"name": "  ", "listeners": "1"},
                    {"name": "Garbled", "listeners": "not-a-number"},
                ]
            }
        }
    }
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    candidates = search_lastfm_artist("anyone", session=fake_session)
    names = [c.name for c in candidates]
    assert names == ["Good", "Garbled"]
    assert candidates[1].listener_count == 0


# ---------------------------------------------------------------------------
# fetch_artist_info_by_name
# ---------------------------------------------------------------------------


def test_fetch_by_name_returns_parsed_info() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_name("boygenius", session=fake_session)

    assert info is not None
    assert info.name == "boygenius"
    assert info.mbid == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"
    assert info.listener_count == 523412
    assert info.url == "https://www.last.fm/music/boygenius"
    tag_names = [t["name"] for t in info.tags]
    assert "indie rock" in tag_names
    assert info.bio_summary is not None
    assert "boygenius" in info.bio_summary


def test_fetch_by_name_sends_autocorrect_param() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    fetch_artist_info_by_name("phoebe bridgers", session=fake_session)

    call = fake_session.get.call_args
    assert call.kwargs["params"]["method"] == "artist.getInfo"
    assert call.kwargs["params"]["autocorrect"] == 1
    assert call.kwargs["params"]["artist"] == "phoebe bridgers"


def test_fetch_by_name_returns_none_when_artist_not_found() -> None:
    payload = _load_fixture("artist_not_found.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_name("nobody at all", session=fake_session)
    assert info is None


def test_fetch_by_name_returns_none_for_blank_input() -> None:
    fake_session = MagicMock()
    assert fetch_artist_info_by_name("   ", session=fake_session) is None
    fake_session.get.assert_not_called()


def test_fetch_by_name_raises_api_error_on_503() -> None:
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(status_code=503, json_payload={})

    with pytest.raises(LastFMAPIError):
        fetch_artist_info_by_name("boygenius", session=fake_session)


def test_fetch_by_name_handles_missing_tags_section() -> None:
    payload = {
        "artist": {
            "name": "Tagless",
            "mbid": "",
            "url": "https://x",
            "stats": {"listeners": "0"},
        }
    }
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_name("tagless", session=fake_session)
    assert info is not None
    assert info.tags == []
    assert info.bio_summary is None


def test_fetch_by_name_handles_single_tag_dict() -> None:
    """Last.fm collapses a single-tag list into a bare dict."""
    payload = {
        "artist": {
            "name": "OneTag",
            "mbid": "",
            "url": "https://x",
            "stats": {"listeners": "10"},
            "tags": {
                "tag": {"name": "indie", "url": "https://t/indie"},
            },
        }
    }
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_name("onetag", session=fake_session)
    assert info is not None
    assert len(info.tags) == 1
    assert info.tags[0]["name"] == "indie"


# ---------------------------------------------------------------------------
# fetch_artist_info_by_mbid
# ---------------------------------------------------------------------------


def test_fetch_by_mbid_returns_parsed_info() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_mbid(
        "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3",
        session=fake_session,
    )

    assert info is not None
    assert info.name == "boygenius"
    assert info.mbid == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"


def test_fetch_by_mbid_sends_mbid_param_not_artist_name() -> None:
    payload = _load_fixture("artist_boygenius.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    fetch_artist_info_by_mbid("abc-mbid", session=fake_session)

    call = fake_session.get.call_args
    assert call.kwargs["params"]["method"] == "artist.getInfo"
    assert call.kwargs["params"]["mbid"] == "abc-mbid"
    assert "artist" not in call.kwargs["params"]


def test_fetch_by_mbid_returns_none_when_not_in_lastfm() -> None:
    payload = _load_fixture("artist_not_found.json")
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_response(json_payload=payload)

    info = fetch_artist_info_by_mbid("missing-mbid", session=fake_session)
    assert info is None


def test_fetch_by_mbid_returns_none_for_blank_input() -> None:
    fake_session = MagicMock()
    assert fetch_artist_info_by_mbid("   ", session=fake_session) is None
    fake_session.get.assert_not_called()


# ---------------------------------------------------------------------------
# find_best_match
# ---------------------------------------------------------------------------


def _candidate(
    *,
    name: str = "Some Artist",
    listeners: int = 1000,
    mbid: str | None = None,
    url: str = "https://x",
) -> LastFMCandidate:
    return LastFMCandidate(
        name=name,
        mbid=mbid,
        listener_count=listeners,
        url=url,
    )


def test_find_best_match_returns_top_candidate_above_threshold() -> None:
    candidates = [_candidate(name="boygenius", listeners=500_000)]
    result = find_best_match("boygenius", candidates)
    assert result is not None
    candidate, confidence = result
    assert candidate.name == "boygenius"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_find_best_match_returns_none_for_empty_candidates() -> None:
    assert find_best_match("anyone", []) is None


def test_find_best_match_returns_none_when_all_below_threshold() -> None:
    candidates = [
        _candidate(name="Completely Different Name", listeners=10),
    ]
    assert find_best_match("boygenius", candidates) is None


def test_find_best_match_listener_count_breaks_ties_between_similar_names() -> None:
    """Two candidates with similar names — the higher-listener one wins.

    Both names have similar SequenceMatcher ratios against the query;
    listener percentile separates them and pushes the popular one over
    the line.
    """
    candidates = [
        _candidate(name="Phoebe Bridgers", listeners=2_000_000),
        _candidate(name="Phoebee Bridger", listeners=50),
    ]
    result = find_best_match("Phoebe Bridgers", candidates)
    assert result is not None
    assert result[0].name == "Phoebe Bridgers"


def test_find_best_match_caps_confidence_at_one() -> None:
    candidates = [_candidate(name="boygenius", listeners=10_000_000)]
    result = find_best_match("boygenius", candidates)
    assert result is not None
    assert result[1] <= 1.0


def test_find_best_match_handles_zero_listeners_gracefully() -> None:
    """All-zero listeners shouldn't NaN the percentile math."""
    candidates = [
        _candidate(name="boygenius", listeners=0),
    ]
    result = find_best_match("boygenius", candidates)
    # Name match perfect (1.0) * 0.7 + listener percentile 0 * 0.3 = 0.7
    # below threshold (0.75), so None.
    assert result is None


def test_confidence_threshold_constant() -> None:
    assert CONFIDENCE_THRESHOLD == 0.75


def test_lastfm_api_error_default_status_code() -> None:
    err = LastFMAPIError("boom")
    assert err.status_code == 0
    assert "boom" in str(err)
