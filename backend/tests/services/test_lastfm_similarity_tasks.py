"""Unit tests for :mod:`backend.services.lastfm_similarity_tasks`.

The Celery wrappers are exercised with a stubbed session factory, a
fake redis client, and patched HTTP calls so each test runs in
milliseconds and does not touch the network or database.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import lastfm_similarity_tasks as tasks
from backend.services.lastfm import LastFMAPIError, LastFMSimilarArtist


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row used by the similarity task."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Phoebe Bridgers"
    musicbrainz_id: str | None = None
    lastfm_similar_enriched_at: datetime | None = None


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


def _similar(name: str, score: float = 0.9) -> LastFMSimilarArtist:
    return LastFMSimilarArtist(
        name=name,
        mbid=None,
        match_score=score,
        url=f"https://www.last.fm/music/{name.replace(' ', '+')}",
        image_url=None,
    )


# ---------------------------------------------------------------------------
# enrich_artist_similarity_from_lastfm
# ---------------------------------------------------------------------------


def test_enrich_returns_missing_when_artist_id_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: None)

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(uuid.uuid4()))

    assert result["status"] == "missing"
    session.commit.assert_not_called()


def test_enrich_skips_recently_enriched_artist(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(
        lastfm_similar_enriched_at=datetime.now(UTC) - timedelta(days=5),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    by_name_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_name", by_name_mock)

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    assert result["status"] == "skipped"
    by_name_mock.assert_not_called()
    assert fake_redis.lock_calls == []


def test_enrich_uses_mbid_lookup_when_musicbrainz_id_set(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id="mb-known-1")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    by_mbid_mock = MagicMock(
        return_value=[_similar("Lucy Dacus", 0.95), _similar("Julien Baker", 0.9)]
    )
    by_name_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_mbid", by_mbid_mock)
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_name", by_name_mock)
    store_mock = MagicMock()
    mark_mock = MagicMock()
    monkeypatch.setattr(tasks, "store_similar_artists", store_mock)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_lastfm_similar_enriched", mark_mock
    )

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "mbid"
    assert result["similar_count"] == 2
    by_mbid_mock.assert_called_once_with("mb-known-1", tasks.SIMILAR_LIMIT)
    by_name_mock.assert_not_called()
    store_mock.assert_called_once()
    session.commit.assert_called_once()


def test_enrich_falls_back_to_name_lookup_when_no_mbid(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    by_mbid_mock = MagicMock()
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_mbid", by_mbid_mock)
    monkeypatch.setattr(
        tasks,
        "fetch_similar_artists_by_name",
        lambda _n: [_similar("Lucy Dacus")],
    )
    monkeypatch.setattr(tasks, "store_similar_artists", MagicMock())
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_lastfm_similar_enriched", MagicMock()
    )

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "name"
    by_mbid_mock.assert_not_called()


def test_enrich_falls_back_to_name_when_mbid_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    """MBID lookup returning an empty list should still try name search."""
    artist = _FakeArtist(musicbrainz_id="mb-not-in-lastfm")
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    monkeypatch.setattr(tasks, "fetch_similar_artists_by_mbid", lambda *_a, **_k: [])
    name_mock = MagicMock(return_value=[_similar("Lucy Dacus")])
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_name", name_mock)
    monkeypatch.setattr(tasks, "store_similar_artists", MagicMock())
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_lastfm_similar_enriched", MagicMock()
    )

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    assert result["status"] == "matched"
    assert result["source"] == "name"
    name_mock.assert_called_once_with(artist.name)


def test_enrich_marks_unmatched_when_no_similar_artists_found(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(tasks, "fetch_similar_artists_by_name", lambda _n: [])
    store_mock = MagicMock()
    mark_mock = MagicMock()
    monkeypatch.setattr(tasks, "store_similar_artists", store_mock)
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_lastfm_similar_enriched", mark_mock
    )

    result = tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    assert result["status"] == "unmatched"
    # No store call when no similar artists came back.
    store_mock.assert_not_called()
    # But the gating column must be stamped so we don't keep re-querying.
    mark_mock.assert_called_once()
    session.commit.assert_called_once()


def test_enrich_lets_api_error_propagate_for_celery_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
) -> None:
    artist = _FakeArtist(musicbrainz_id=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    def boom(_n: str) -> list[LastFMSimilarArtist]:
        raise LastFMAPIError("503", status_code=503)

    monkeypatch.setattr(tasks, "fetch_similar_artists_by_name", boom)

    with pytest.raises(LastFMAPIError):
        tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

    session.rollback.assert_called_once()


def test_enrich_celery_task_is_configured_for_retries() -> None:
    task = tasks.enrich_artist_similarity_from_lastfm
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
    monkeypatch.setattr(
        tasks,
        "fetch_similar_artists_by_mbid",
        lambda *_a, **_k: [_similar("Lucy Dacus")],
    )
    monkeypatch.setattr(tasks, "store_similar_artists", MagicMock())
    monkeypatch.setattr(
        tasks.artists_repo, "mark_artist_lastfm_similar_enriched", MagicMock()
    )

    tasks.enrich_artist_similarity_from_lastfm.run(str(artist.id))

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
# backfill_lastfm_similarity_enrichment
# ---------------------------------------------------------------------------


def test_backfill_queues_one_task_per_pending_artist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    pending = [_FakeArtist(name=f"Act {i}") for i in range(3)]
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_lastfm_similar_enrichment",
        lambda _s, limit: pending,
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_lastfm_similarity_enrichment.run()

    assert result == {"queued": 3}
    assert send_mock.call_count == 3
    sent_ids = [
        call.kwargs.get("args", call.args[1] if len(call.args) > 1 else [None])[0]
        for call in send_mock.call_args_list
    ]
    assert set(sent_ids) == {str(a.id) for a in pending}


def test_backfill_returns_zero_when_no_unenriched_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_lastfm_similar_enrichment",
        lambda _s, limit: [],
    )
    send_mock = MagicMock()
    monkeypatch.setattr(tasks.celery_app, "send_task", send_mock)

    result = tasks.backfill_lastfm_similarity_enrichment.run()

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
        "list_artists_for_lastfm_similar_enrichment",
        list_mock,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", MagicMock())

    tasks.backfill_lastfm_similarity_enrichment.run()

    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs.get("limit") == tasks.BACKFILL_BATCH_SIZE


# ---------------------------------------------------------------------------
# resolve_unlinked_similarity_rows
# ---------------------------------------------------------------------------


def test_resolve_runs_and_logs_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    resolve_mock = MagicMock(return_value=7)
    monkeypatch.setattr(tasks, "resolve_similarity_links", resolve_mock)

    result = tasks.resolve_unlinked_similarity_rows.run()

    assert result == {"linked": 7}
    resolve_mock.assert_called_once()
    session.commit.assert_called_once()


def test_resolve_returns_zero_when_no_links_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks, "resolve_similarity_links", lambda _s: 0)

    result = tasks.resolve_unlinked_similarity_rows.run()
    assert result == {"linked": 0}
