"""Unit tests for :mod:`backend.services.genre_normalization_tasks`.

Both Celery wrappers run with a stubbed session factory so each case
exercises the task's orchestration in isolation: no database, no
network, no real Celery broker. Behavior tested:

* ``normalize_artist_genres`` reads MusicBrainz + Last.fm signals,
  forwards them to the pure-Python normalizer, persists the canonical
  output, and reports a structured outcome.
* ``backfill_genre_normalization`` walks every pending row inline (no
  fanout — pure compute), counts with-genres vs empty rows, and
  honors the ``force`` flag.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import genre_normalization_tasks as tasks


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row used by the normalization path."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "boygenius"
    musicbrainz_genres: list[dict[str, Any]] | None = None
    musicbrainz_tags: list[dict[str, Any]] | None = None
    lastfm_tags: list[dict[str, Any]] | None = None


class _CtxSession:
    """Minimal session stub that supports ``with`` plus commit/rollback."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.flush = MagicMock()

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


# ---------------------------------------------------------------------------
# _gather_musicbrainz_signal
# ---------------------------------------------------------------------------


def test_gather_musicbrainz_signal_concatenates_genres_and_tags() -> None:
    artist = _FakeArtist(
        musicbrainz_genres=[{"name": "indie rock", "count": 7}],
        musicbrainz_tags=[{"name": "folk", "count": 4}],
    )

    out = tasks._gather_musicbrainz_signal(artist)  # type: ignore[arg-type]

    assert out == [
        {"name": "indie rock", "count": 7},
        {"name": "folk", "count": 4},
    ]


def test_gather_musicbrainz_signal_handles_none_columns() -> None:
    artist = _FakeArtist(musicbrainz_genres=None, musicbrainz_tags=None)

    assert tasks._gather_musicbrainz_signal(artist) == []  # type: ignore[arg-type]


def test_gather_musicbrainz_signal_handles_only_one_side_populated() -> None:
    """Sprint 1A wrote tags-only rows for some artists; cover that path."""
    artist = _FakeArtist(
        musicbrainz_genres=None,
        musicbrainz_tags=[{"name": "jazz", "count": 5}],
    )

    out = tasks._gather_musicbrainz_signal(artist)  # type: ignore[arg-type]

    assert out == [{"name": "jazz", "count": 5}]


# ---------------------------------------------------------------------------
# normalize_artist_genres
# ---------------------------------------------------------------------------


def test_normalize_artist_genres_returns_missing_when_artist_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: None)
    mark_mock = MagicMock()
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", mark_mock)

    artist_id = str(uuid.uuid4())
    result = tasks.normalize_artist_genres.run(artist_id)

    assert result == {"artist_id": artist_id, "status": "missing"}
    mark_mock.assert_not_called()
    session.commit.assert_not_called()


def test_normalize_artist_genres_persists_canonical_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(
        musicbrainz_genres=[{"name": "indie rock", "count": 9}],
        musicbrainz_tags=[{"name": "folk", "count": 3}],
        lastfm_tags=[
            {"name": "indie rock", "url": "u1"},
            {"name": "folk", "url": "u2"},
        ],
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", mark_mock)

    result = tasks.normalize_artist_genres.run(str(artist.id))

    assert result["status"] == "normalized"
    assert result["artist_id"] == str(artist.id)
    assert result["genre_count"] >= 1
    mark_mock.assert_called_once()
    kwargs = mark_mock.call_args.kwargs
    assert "Indie Rock" in kwargs["canonical_genres"]
    assert "Folk" in kwargs["canonical_genres"]
    assert set(kwargs["genre_confidence"].keys()) == set(kwargs["canonical_genres"])
    for value in kwargs["genre_confidence"].values():
        assert 0.0 <= value <= 1.0
    session.commit.assert_called_once()


def test_normalize_artist_genres_returns_empty_when_no_canonical_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An artist with all-noise tags still gets stamped — `empty` status."""
    artist = _FakeArtist(
        musicbrainz_genres=[{"name": "seen live", "count": 1}],
        musicbrainz_tags=[{"name": "favorites", "count": 2}],
        lastfm_tags=[{"name": "albums I own", "url": "u"}],
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", mark_mock)

    result = tasks.normalize_artist_genres.run(str(artist.id))

    assert result["status"] == "empty"
    assert result["genre_count"] == 0
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["canonical_genres"] == []
    assert kwargs["genre_confidence"] == {}
    session.commit.assert_called_once()


def test_normalize_artist_genres_handles_all_none_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-enrichment row: every source column is still None."""
    artist = _FakeArtist(
        musicbrainz_genres=None,
        musicbrainz_tags=None,
        lastfm_tags=None,
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", mark_mock)

    result = tasks.normalize_artist_genres.run(str(artist.id))

    assert result["status"] == "empty"
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["canonical_genres"] == []
    assert kwargs["genre_confidence"] == {}


def test_normalize_artist_genres_is_idempotent_across_repeat_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two back-to-back runs must produce the same canonical output."""
    artist = _FakeArtist(
        musicbrainz_genres=[{"name": "hip hop", "count": 8}],
        lastfm_tags=[{"name": "hip hop", "url": "u"}],
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    captured: list[dict[str, Any]] = []

    def capture(_s: Any, a: Any, **kw: Any) -> Any:
        captured.append(kw)
        return a

    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", capture)

    first = tasks.normalize_artist_genres.run(str(artist.id))
    second = tasks.normalize_artist_genres.run(str(artist.id))

    assert first == second
    assert captured[0]["canonical_genres"] == captured[1]["canonical_genres"]
    assert captured[0]["genre_confidence"] == captured[1]["genre_confidence"]


def test_normalize_artist_genres_rolls_back_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist(
        musicbrainz_genres=[{"name": "indie rock", "count": 9}],
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", boom)

    with pytest.raises(RuntimeError):
        tasks.normalize_artist_genres.run(str(artist.id))

    session.rollback.assert_called_once()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# backfill_genre_normalization
# ---------------------------------------------------------------------------


def test_backfill_processes_every_pending_artist_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No fan-out: the backfill normalizes each pending row in-place."""
    pending = [
        _FakeArtist(
            name="Boygenius",
            musicbrainz_genres=[{"name": "indie rock", "count": 9}],
        ),
        _FakeArtist(
            name="Honey Dijon",
            musicbrainz_genres=[{"name": "house", "count": 6}],
        ),
        _FakeArtist(
            name="Mystery Act",
            musicbrainz_genres=[{"name": "seen live", "count": 1}],
        ),
    ]
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_genre_normalization",
        lambda _s, *, limit, force: pending,
    )
    captured: list[dict[str, Any]] = []

    def capture(_s: Any, a: Any, **kw: Any) -> Any:
        captured.append({"artist": a, **kw})
        return a

    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", capture)

    result = tasks.backfill_genre_normalization.run()

    assert result["processed"] == 3
    assert result["with_genres"] == 2  # boygenius + honey dijon
    assert result["empty"] == 1  # mystery act
    assert len(captured) == 3
    session.commit.assert_called_once()


def test_backfill_returns_zero_counts_when_nothing_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_genre_normalization",
        lambda _s, *, limit, force: [],
    )
    mark_mock = MagicMock()
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_genres_normalized", mark_mock)

    result = tasks.backfill_genre_normalization.run()

    assert result == {"processed": 0, "with_genres": 0, "empty": 0}
    mark_mock.assert_not_called()
    session.commit.assert_called_once()


def test_backfill_default_call_passes_force_false_and_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    list_mock = MagicMock(return_value=[])
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "list_artists_for_genre_normalization", list_mock
    )
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_genres_normalized", MagicMock()
    )

    tasks.backfill_genre_normalization.run()

    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs == {
        "limit": tasks.BACKFILL_BATCH_SIZE,
        "force": False,
    }


def test_backfill_force_flag_re_normalizes_every_artist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``force=True`` is forwarded to the repo so already-normalized
    rows get re-processed after a mapping-dictionary change."""
    session = _CtxSession()
    list_mock = MagicMock(return_value=[])
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "list_artists_for_genre_normalization", list_mock
    )
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_genres_normalized", MagicMock()
    )

    result = tasks.backfill_genre_normalization.run(force=True)

    assert result == {"processed": 0, "with_genres": 0, "empty": 0}
    assert list_mock.call_args.kwargs["force"] is True


def test_backfill_batch_size_safety_bound() -> None:
    """The ceiling is a safety bound, not a per-fire intent."""
    assert tasks.BACKFILL_BATCH_SIZE == 5000
