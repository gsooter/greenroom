"""Unit tests for :mod:`backend.services.lastfm_tasks`.

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

from backend.services import lastfm_tasks as tasks
from backend.services.lastfm import (
    LastFMAPIError,
    LastFMArtistInfo,
    LastFMCandidate,
)


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "boygenius"
    musicbrainz_id: str | None = None
    lastfm_tags: list[dict[str, Any]] | None = None
    lastfm_listener_count: int | None = None
    lastfm_url: str | None = None
    lastfm_bio_summary: str | None = None
    lastfm_enriched_at: datetime | None = None
    lastfm_match_confidence: Decimal | None = None


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
    """Skip the real 250ms pacing sleep in every test."""
    monkeypatch.setattr(tasks.time, "sleep", lambda _s: None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    client = _FakeRedis()
    monkeypatch.setattr(tasks, "_get_redis", lambda: client)
    return client


def _candidate(*, name: str = "boygenius", listeners: int = 100_000) -> LastFMCandidate:
    return LastFMCandidate(
        name=name,
        mbid=None,
        listener_count=listeners,
        url="https://www.last.fm/music/boygenius",
    )


def _info(
    *,
    name: str = "boygenius",
    mbid: str | None = "mb-1",
    listeners: int = 100_000,
    tags: int = 5,
    url: str = "https://www.last.fm/music/boygenius",
    bio: str | None = "an indie supergroup",
) -> LastFMArtistInfo:
    return LastFMArtistInfo(
        name=name,
        mbid=mbid,
        listener_count=listeners,
        url=url,
        tags=[{"name": f"tag{i}", "url": f"u{i}"} for i in range(tags)],
        bio_summary=bio,
    )


# ---------------------------------------------------------------------------
# enrich_artist_from_lastfm
# ---------------------------------------------------------------------------


def test_enrich_returns_missing_when_artist_id_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: None)

    result = tasks.enrich_artist_from_lastfm.run(str(uuid.uuid4()))

    assert result["status"] == "missing"
    session.commit.assert_not_called()


def test_enrich_skips_recently_enriched_artist(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(
        lastfm_enriched_at=datetime.now(UTC) - timedelta(days=5),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    by_name_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", by_name_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "skipped"
    by_name_mock.assert_not_called()
    assert fake_redis.lock_calls == []


def test_enrich_does_not_skip_artist_enriched_long_ago(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """A row last enriched > 30d ago is eligible again."""
    artist = _FakeArtist(
        lastfm_enriched_at=datetime.now(UTC) - timedelta(days=60),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_lastfm_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.9))
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", lambda _n: _info())
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"


def test_enrich_uses_mbid_lookup_when_musicbrainz_id_set(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id="mb-known-1")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    by_mbid_mock = MagicMock(return_value=_info(mbid="mb-known-1", tags=4))
    by_name_mock = MagicMock()
    search_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_artist_info_by_mbid", by_mbid_mock)
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", by_name_mock)
    monkeypatch.setattr(tasks, "search_lastfm_artist", search_mock)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "mbid"
    by_mbid_mock.assert_called_once_with("mb-known-1")
    # MBID match short-circuits — name search and name fetch never run.
    by_name_mock.assert_not_called()
    search_mock.assert_not_called()
    # MBID lookup is exact -> confidence 1.0
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["confidence"] == Decimal("1.00")
    assert kwargs["listener_count"] == 100_000
    assert len(kwargs["tags"]) == 4


def test_enrich_falls_back_to_name_search_when_no_mbid(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    by_mbid_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_artist_info_by_mbid", by_mbid_mock)
    monkeypatch.setattr(tasks, "search_lastfm_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.88))
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", lambda _n: _info())
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "name"
    by_mbid_mock.assert_not_called()
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["confidence"] == Decimal("0.88")


def test_enrich_falls_back_to_name_when_mbid_lookup_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """MBID lookup miss must still try name-based search."""
    artist = _FakeArtist(musicbrainz_id="mb-not-in-lastfm")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    monkeypatch.setattr(tasks, "fetch_artist_info_by_mbid", lambda _m: None)
    search_mock = MagicMock(return_value=[_candidate()])
    monkeypatch.setattr(tasks, "search_lastfm_artist", search_mock)
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.85))
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", lambda _n: _info())
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "name"
    search_mock.assert_called_once_with("boygenius")


def test_enrich_stores_tags_listener_count_url_and_bio_on_match(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id="mb-1")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    info = _info(
        listeners=900_000,
        tags=10,
        url="https://www.last.fm/music/X",
        bio="bio blurb",
    )
    monkeypatch.setattr(tasks, "fetch_artist_info_by_mbid", lambda _m: info)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    tasks.enrich_artist_from_lastfm.run(str(artist.id))

    kwargs = mark_mock.call_args.kwargs
    assert len(kwargs["tags"]) == 10
    assert kwargs["listener_count"] == 900_000
    assert kwargs["url"] == "https://www.last.fm/music/X"
    assert kwargs["bio_summary"] == "bio blurb"
    assert kwargs["confidence"] == Decimal("1.00")
    session.commit.assert_called_once()


def test_enrich_marks_unmatched_when_no_mbid_and_no_confident_name_match(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_lastfm_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: None)
    fetch_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", fetch_mock)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "unmatched"
    fetch_mock.assert_not_called()
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["tags"] is None
    assert kwargs["listener_count"] is None
    assert kwargs["url"] is None
    assert kwargs["bio_summary"] is None
    assert kwargs["confidence"] is None
    session.commit.assert_called_once()


def test_enrich_marks_unmatched_when_name_lookup_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """find_best_match picks a candidate but getInfo says not found."""
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "search_lastfm_artist", lambda _n: [_candidate()])
    monkeypatch.setattr(tasks, "find_best_match", lambda _n, _c: (_candidate(), 0.9))
    monkeypatch.setattr(tasks, "fetch_artist_info_by_name", lambda _n: None)
    mark_mock = MagicMock(return_value=artist)
    monkeypatch.setattr(tasks.artists_repo, "mark_artist_lastfm_enriched", mark_mock)

    result = tasks.enrich_artist_from_lastfm.run(str(artist.id))

    assert result["status"] == "unmatched"
    kwargs = mark_mock.call_args.kwargs
    assert kwargs["tags"] is None


def test_enrich_lets_api_error_propagate_for_celery_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """``LastFMAPIError`` is the retryable exception the Celery
    decorator is configured to autoretry on. The task itself should
    not catch it; it must propagate so Celery's machinery can retry.
    """
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    def boom(_n: str) -> list[LastFMCandidate]:
        raise LastFMAPIError("503", status_code=503)

    monkeypatch.setattr(tasks, "search_lastfm_artist", boom)

    with pytest.raises(LastFMAPIError):
        tasks.enrich_artist_from_lastfm.run(str(artist.id))

    session.rollback.assert_called_once()


def test_enrich_celery_task_is_configured_for_retries() -> None:
    """The retry policy the spec asks for is encoded on the task itself."""
    task = tasks.enrich_artist_from_lastfm
    assert task.max_retries == 3
    assert LastFMAPIError in task.autoretry_for
    assert task.retry_backoff is True
    assert task.retry_jitter is True


def test_enrich_acquires_rate_limit_lock(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id="mb-1")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "fetch_artist_info_by_mbid", lambda _m: _info())
    monkeypatch.setattr(
        tasks.artists_repo,
        "mark_artist_lastfm_enriched",
        lambda _s, a, **_kw: a,
    )

    tasks.enrich_artist_from_lastfm.run(str(artist.id))

    # The single MBID HTTP call should have been wrapped in a lock.
    assert len(fake_redis.lock_calls) == 1
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


def test_paced_call_uses_250ms_interval() -> None:
    """Last.fm pacing — 4 req/sec target, 250ms between requests."""
    assert pytest.approx(0.25) == tasks.RATE_LIMIT_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# backfill_lastfm_enrichment
# ---------------------------------------------------------------------------


def test_backfill_queues_one_task_per_pending_artist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    pending = [_FakeArtist(name=f"Act {i}") for i in range(3)]
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_lastfm_enrichment",
        lambda _s, limit: pending,
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_lastfm_enrichment.run()

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
        "list_artists_for_lastfm_enrichment",
        lambda _s, limit: [],
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_lastfm_enrichment.run()

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
        "list_artists_for_lastfm_enrichment",
        list_mock,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", MagicMock())

    tasks.backfill_lastfm_enrichment.run()

    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs.get("limit") == tasks.BACKFILL_BATCH_SIZE


def test_backfill_batch_size_drains_current_backlog() -> None:
    """Sized to clear the full artist set in one nightly fire.

    Set high enough that one beat run drains the unenriched backlog,
    paced naturally by the Last.fm rate limiter (~250 ms/request).
    """
    assert tasks.BACKFILL_BATCH_SIZE == 2000
