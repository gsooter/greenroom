"""Unit tests for :mod:`backend.services.musicbrainz_tasks`.

The Celery wrappers are exercised with a stubbed session factory, a
fake redis client, and patched HTTP calls so each test runs in
milliseconds and does not touch the network or database.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import musicbrainz_tasks as tasks
from backend.services.musicbrainz import (
    MusicBrainzAPIError,
    MusicBrainzArtistDetails,
    MusicBrainzCandidate,
    MusicBrainzNotFoundError,
)


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "boygenius"
    musicbrainz_id: str | None = None
    musicbrainz_genres: list[dict[str, Any]] | None = None
    musicbrainz_tags: list[dict[str, Any]] | None = None
    musicbrainz_enriched_at: datetime | None = None
    musicbrainz_match_confidence: Decimal | None = None


class _CtxSession:
    """Minimal session that supports ``with`` plus commit/rollback mocks."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.flush = MagicMock()

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeLock:
    """No-op stand-in for ``redis.Redis.lock`` context manager."""

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    def __enter__(self) -> _FakeLock:
        self.entered += 1
        return self

    def __exit__(self, *_exc: object) -> None:
        self.exited += 1


class _FakeRedis:
    """Stub redis client that records lock acquisition calls."""

    def __init__(self) -> None:
        self.lock_calls: list[dict[str, Any]] = []
        self._lock = _FakeLock()

    def lock(self, key: str, **kwargs: Any) -> _FakeLock:
        self.lock_calls.append({"key": key, **kwargs})
        return self._lock


@pytest.fixture(autouse=True)
def _patch_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real 1.1s pacing sleep in every test."""
    monkeypatch.setattr(tasks.time, "sleep", lambda _s: None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    client = _FakeRedis()
    monkeypatch.setattr(tasks, "_get_redis", lambda: client)
    return client


def _candidate(
    *, mbid: str = "mb-1", name: str = "boygenius", score: int = 100
) -> MusicBrainzCandidate:
    return MusicBrainzCandidate(
        mbid=mbid,
        name=name,
        score=score,
        disambiguation=None,
        country=None,
        type=None,
    )


def _details(
    *, mbid: str = "mb-1", genres: int = 3, tags: int = 2
) -> MusicBrainzArtistDetails:
    return MusicBrainzArtistDetails(
        mbid=mbid,
        name="boygenius",
        genres=[{"name": f"g{i}", "count": i + 1} for i in range(genres)],
        tags=[{"name": f"t{i}", "count": i + 1} for i in range(tags)],
    )


# ---------------------------------------------------------------------------
# enrich_artist_from_musicbrainz
# ---------------------------------------------------------------------------


def test_enrich_returns_missing_when_artist_id_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: None)

    result = tasks.enrich_artist_from_musicbrainz.run(str(uuid.uuid4()))

    assert result["status"] == "missing"
    session.commit.assert_not_called()


def test_enrich_skips_recently_enriched_artist(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(
        musicbrainz_enriched_at=datetime.now(UTC) - timedelta(days=5),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    search_mock = MagicMock()
    monkeypatch.setattr(tasks, "search_musicbrainz_artist", search_mock)

    result = tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    assert result["status"] == "skipped"
    search_mock.assert_not_called()
    assert fake_redis.lock_calls == []


def test_enrich_does_not_skip_artist_enriched_long_ago(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """A row last enriched > 30d ago is eligible again."""
    artist = _FakeArtist(
        musicbrainz_enriched_at=datetime.now(UTC) - timedelta(days=60),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_musicbrainz_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.95))
    monkeypatch.setattr(tasks, "fetch_artist_details", lambda _m: _details())
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_musicbrainz_enriched", mark_mock
    )

    result = tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    assert result["status"] == "matched"


def test_enrich_stores_genres_and_tags_on_match(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_musicbrainz_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(
        tasks, "find_best_match", lambda _n, _c: (_candidate(mbid="mb-1"), 0.92)
    )
    monkeypatch.setattr(tasks, "fetch_artist_details", lambda _m: _details(genres=3))
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_musicbrainz_enriched", mark_mock
    )

    result = tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["musicbrainz_id"] == "mb-1"
    assert result["genre_count"] == 3
    assert result["confidence"] == pytest.approx(0.92)

    kwargs = mark_mock.call_args.kwargs
    assert kwargs["musicbrainz_id"] == "mb-1"
    assert len(kwargs["genres"]) == 3
    assert len(kwargs["tags"]) == 2
    assert kwargs["confidence"] == Decimal("0.92")
    session.commit.assert_called_once()


def test_enrich_marks_unmatched_when_no_confident_candidate(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(
        tasks, "search_musicbrainz_artist", lambda _n: [_candidate(score=10)]
    )
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: None)
    fetch_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_artist_details", fetch_mock)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_musicbrainz_enriched", mark_mock
    )

    result = tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    assert result["status"] == "unmatched"
    fetch_mock.assert_not_called()
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["musicbrainz_id"] is None
    assert kwargs["genres"] is None
    assert kwargs["tags"] is None
    assert kwargs["confidence"] is None
    session.commit.assert_called_once()


def test_enrich_marks_not_found_when_mbid_lookup_404s(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_musicbrainz_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.91))

    def boom(_mbid: str) -> MusicBrainzArtistDetails:
        raise MusicBrainzNotFoundError("missing")

    monkeypatch.setattr(tasks, "fetch_artist_details", boom)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_musicbrainz_enriched", mark_mock
    )

    result = tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    assert result["status"] == "not_found"
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["musicbrainz_id"] is None
    session.commit.assert_called_once()


def test_enrich_lets_api_error_propagate_for_celery_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """``MusicBrainzAPIError`` is the retryable exception the Celery
    decorator is configured to autoretry on. The task itself should
    not catch it; it must propagate so Celery's machinery can retry.
    """
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    def boom(_n: str) -> list[MusicBrainzCandidate]:
        raise MusicBrainzAPIError("503", status_code=503)

    monkeypatch.setattr(tasks, "search_musicbrainz_artist", boom)

    with pytest.raises(MusicBrainzAPIError):
        tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    session.rollback.assert_called_once()


def test_enrich_celery_task_is_configured_for_retries() -> None:
    """The retry policy the spec asks for is encoded on the task itself."""
    task = tasks.enrich_artist_from_musicbrainz
    assert task.max_retries == 3
    assert MusicBrainzAPIError in task.autoretry_for
    assert task.retry_backoff is True
    assert task.retry_jitter is True


def test_enrich_acquires_rate_limit_lock(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_musicbrainz_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.9))
    monkeypatch.setattr(tasks, "fetch_artist_details", lambda _m: _details())
    monkeypatch.setattr(
        tasks.artists_repo,
        "mark_artist_musicbrainz_enriched",
        lambda _s, a, **_kw: a,
    )

    tasks.enrich_artist_from_musicbrainz.run(str(artist.id))

    # Both HTTP calls should have been wrapped in a lock acquisition.
    assert len(fake_redis.lock_calls) == 2
    for call in fake_redis.lock_calls:
        assert call["key"] == tasks.RATE_LIMIT_LOCK_KEY


def test_paced_call_sleeps_inside_lock(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """Sanity-check the pacing primitive: lock first, then sleep, then call."""
    order: list[str] = []

    @contextmanager
    def fake_lock(*_a: Any, **_kw: Any) -> Any:
        order.append("lock-enter")
        yield
        order.append("lock-exit")

    fake_redis.lock = fake_lock  # type: ignore[assignment]

    def fake_sleep(_s: float) -> None:
        order.append("sleep")

    monkeypatch.setattr(tasks.time, "sleep", fake_sleep)

    def func() -> str:
        order.append("call")
        return "ok"

    out = tasks._paced_call(func)
    assert out == "ok"
    assert order == ["lock-enter", "sleep", "call", "lock-exit"]


# ---------------------------------------------------------------------------
# backfill_musicbrainz_enrichment
# ---------------------------------------------------------------------------


def test_backfill_queues_one_task_per_pending_artist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    pending = [_FakeArtist(name=f"Act {i}") for i in range(3)]
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_musicbrainz_enrichment",
        lambda _s, limit: pending,
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_musicbrainz_enrichment.run()

    assert result == {"queued": 3}
    assert send_mock.call_count == 3
    sent_artist_ids = [
        call.kwargs.get("args", call.args[1] if len(call.args) > 1 else [None])[0]
        for call in send_mock.call_args_list
    ]
    assert set(sent_artist_ids) == {str(a.id) for a in pending}


def test_backfill_returns_zero_when_no_unenriched_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_musicbrainz_enrichment",
        lambda _s, limit: [],
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_musicbrainz_enrichment.run()

    assert result == {"queued": 0}
    send_mock.assert_not_called()


def test_backfill_passes_batch_size_to_repo_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    list_mock = MagicMock(return_value=[])
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_musicbrainz_enrichment",
        list_mock,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", MagicMock())

    tasks.backfill_musicbrainz_enrichment.run()

    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs.get("limit") == tasks.BACKFILL_BATCH_SIZE
