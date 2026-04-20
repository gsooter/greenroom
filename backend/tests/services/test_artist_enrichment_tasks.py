"""Unit tests for :mod:`backend.services.artist_enrichment_tasks`.

The Celery-layer wrappers are thin — each test replaces the session
factory, the Spotify client, and the decision function
(:func:`enrich_artist`) at the module boundary, then asserts on the
summary dict the task returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import SPOTIFY_AUTH_FAILED, AppError
from backend.services import artist_enrichment_tasks as tasks
from backend.services.spotify import SpotifyTokens


@dataclass
class _FakeArtist:
    """Stand-in artist row — only the fields the task reads."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Phoebe Bridgers"
    spotify_id: str | None = None
    genres: list[str] = field(default_factory=list)


class _CtxSession:
    """Minimal session that supports ``with`` plus commit/rollback mocks."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _tokens() -> SpotifyTokens:
    return SpotifyTokens(
        access_token="app-token",
        refresh_token=None,
        expires_at=datetime.now().astimezone(),
        scope="",
    )


# ---------------------------------------------------------------------------
# enrich_unenriched_artists
# ---------------------------------------------------------------------------


def test_enrich_unenriched_artists_returns_zero_summary_when_no_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "list_unenriched_artists", lambda _s, limit: []
    )
    # Token mint should NOT be called when there's no work.
    token_mock = MagicMock()
    monkeypatch.setattr(tasks.spotify_service, "get_app_access_token", token_mock)

    result = tasks.enrich_unenriched_artists()

    assert result == {
        "processed": 0,
        "matched": 0,
        "unmatched": 0,
        "errors": 0,
        "token_failed": False,
    }
    token_mock.assert_not_called()
    session.commit.assert_not_called()


def test_enrich_unenriched_artists_reports_token_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If we can't mint an app token, return a ``token_failed`` summary."""
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_unenriched_artists",
        lambda _s, limit: [_FakeArtist()],
    )

    def boom() -> SpotifyTokens:
        raise AppError(
            code=SPOTIFY_AUTH_FAILED,
            message="rejected",
            status_code=502,
        )

    monkeypatch.setattr(tasks.spotify_service, "get_app_access_token", boom)

    result = tasks.enrich_unenriched_artists()

    assert result["token_failed"] is True
    assert result["processed"] == 0


def test_enrich_unenriched_artists_counts_matches_and_unmatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each artist goes through search + enrich_artist; summary tallies both."""
    session = _CtxSession()
    matched_artist = _FakeArtist(name="Matched")
    unmatched_artist = _FakeArtist(name="Unmatched")
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_unenriched_artists",
        lambda _s, limit: [matched_artist, unmatched_artist],
    )
    monkeypatch.setattr(
        tasks.spotify_service,
        "get_app_access_token",
        lambda: _tokens(),
    )

    search_mock = MagicMock(return_value=[{"id": "sp-1", "name": "anything"}])
    monkeypatch.setattr(tasks.spotify_service, "search_artist", search_mock)

    def fake_enrich(
        _s: Any, artist: _FakeArtist, *, search_results: list[dict[str, Any]]
    ) -> _FakeArtist:
        if artist is matched_artist:
            artist.spotify_id = "sp-1"
        # unmatched_artist keeps spotify_id = None
        return artist

    monkeypatch.setattr(tasks, "enrich_artist", fake_enrich)

    result = tasks.enrich_unenriched_artists()

    assert result == {
        "processed": 2,
        "matched": 1,
        "unmatched": 1,
        "errors": 0,
        "token_failed": False,
    }
    assert search_mock.call_count == 2
    session.commit.assert_called_once()


def test_enrich_unenriched_artists_continues_past_per_artist_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised exception on one artist is counted, not fatal to the batch."""
    session = _CtxSession()
    artists = [_FakeArtist(name=f"Act {i}") for i in range(3)]
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "list_unenriched_artists", lambda _s, limit: artists
    )
    monkeypatch.setattr(
        tasks.spotify_service, "get_app_access_token", lambda: _tokens()
    )

    def flaky_search(_token: str, name: str) -> list[dict[str, Any]]:
        if name == "Act 1":
            raise AppError(
                code=SPOTIFY_AUTH_FAILED,
                message="rate-limited",
                status_code=429,
            )
        return [{"id": "sp", "name": name}]

    monkeypatch.setattr(tasks.spotify_service, "search_artist", flaky_search)

    def fake_enrich(
        _s: Any, artist: _FakeArtist, *, search_results: list[dict[str, Any]]
    ) -> _FakeArtist:
        artist.spotify_id = "sp"
        return artist

    monkeypatch.setattr(tasks, "enrich_artist", fake_enrich)

    result = tasks.enrich_unenriched_artists()

    assert result["processed"] == 3
    assert result["matched"] == 2
    assert result["errors"] == 1
    assert result["token_failed"] is False
    session.commit.assert_called_once()


def test_enrich_unenriched_artists_respects_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repo lookup is called with ``limit=BATCH_SIZE``."""
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    list_mock = MagicMock(return_value=[])
    monkeypatch.setattr(tasks.artists_repo, "list_unenriched_artists", list_mock)

    tasks.enrich_unenriched_artists()

    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs.get("limit") == tasks.BATCH_SIZE


# ---------------------------------------------------------------------------
# enrich_artist_from_spotify
# ---------------------------------------------------------------------------


def test_enrich_artist_from_spotify_returns_missing_when_id_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _uid: None)
    result = tasks.enrich_artist_from_spotify(str(uuid.uuid4()))
    assert result["status"] == "missing"
    session.commit.assert_not_called()


def test_enrich_artist_from_spotify_happy_path_matched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(name="Phoebe Bridgers")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _uid: artist)
    monkeypatch.setattr(
        tasks.spotify_service, "get_app_access_token", lambda: _tokens()
    )
    monkeypatch.setattr(
        tasks.spotify_service, "search_artist", lambda _t, _n: [{"id": "sp-1"}]
    )

    def fake_enrich(
        _s: Any, a: _FakeArtist, *, search_results: list[dict[str, Any]]
    ) -> _FakeArtist:
        a.spotify_id = "sp-1"
        a.genres = ["indie"]
        return a

    monkeypatch.setattr(tasks, "enrich_artist", fake_enrich)

    result = tasks.enrich_artist_from_spotify(str(artist.id))

    assert result["status"] == "matched"
    assert result["spotify_id"] == "sp-1"
    assert result["genres"] == ["indie"]
    session.commit.assert_called_once()


def test_enrich_artist_from_spotify_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "get_artist_by_id", lambda _s, _uid: _FakeArtist()
    )

    def boom() -> SpotifyTokens:
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks.spotify_service, "get_app_access_token", boom)

    with pytest.raises(RuntimeError):
        tasks.enrich_artist_from_spotify(str(uuid.uuid4()))

    session.rollback.assert_called_once()
    session.commit.assert_not_called()
