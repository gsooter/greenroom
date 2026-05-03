"""Unit tests for :mod:`backend.services.tag_consolidation_tasks`.

The Celery wrappers run with a stubbed session factory and a fake
Redis client so each case exercises the task's orchestration in
isolation: no database, no network, no real Celery broker. The
underlying pure-function pipeline is covered separately in
``tests/services/test_tag_consolidation.py`` and the database-bound
behavior is covered in ``tests/data/test_tag_consolidation_db.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import tag_consolidation_tasks as tasks


@dataclass
class _FakeArtist:
    """Stand-in for the Artist ORM row used by the consolidation path."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Phoebe Bridgers"
    musicbrainz_enriched_at: datetime | None = None
    lastfm_enriched_at: datetime | None = None
    granular_tags_consolidated_at: datetime | None = None


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


class _FakeRedis:
    """Captures setex/get calls without holding any state across tests."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, int, bytes]] = []

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        self.set_calls.append((key, ttl, value))
        self.store[key] = value


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(tasks, "_get_redis", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# consolidate_artist_tags_task
# ---------------------------------------------------------------------------


def test_task_returns_missing_when_artist_id_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: None)

    result = tasks.consolidate_artist_tags_task.run(str(uuid.uuid4()))

    assert result["status"] == "missing"
    session.commit.assert_not_called()


def test_task_skips_when_consolidation_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-op when consolidation is newer than every source enrichment."""
    now = datetime.now(UTC)
    artist = _FakeArtist(
        musicbrainz_enriched_at=now - timedelta(days=2),
        lastfm_enriched_at=now - timedelta(days=2),
        granular_tags_consolidated_at=now - timedelta(hours=1),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    consolidate_mock = MagicMock()
    monkeypatch.setattr(tasks, "consolidate_artist_tags", consolidate_mock)

    result = tasks.consolidate_artist_tags_task.run(str(artist.id))

    assert result["status"] == "skipped"
    consolidate_mock.assert_not_called()


def test_task_reruns_when_source_data_is_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source data newer than consolidation timestamp → re-consolidate."""
    now = datetime.now(UTC)
    artist = _FakeArtist(
        # Last.fm refreshed yesterday; consolidation last ran two days ago.
        lastfm_enriched_at=now - timedelta(days=1),
        granular_tags_consolidated_at=now - timedelta(days=2),
    )
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(
        tasks, "build_global_tag_blocklist", lambda _s, redis_client=None: set()
    )
    monkeypatch.setattr(
        tasks, "consolidate_artist_tags", lambda _s, _id, blocklist=None: ["indie folk"]
    )

    result = tasks.consolidate_artist_tags_task.run(str(artist.id))

    assert result["status"] == "consolidated"
    assert result["tag_count"] == 1
    session.commit.assert_called_once()


def test_task_runs_when_never_consolidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh artist with no consolidation timestamp must run."""
    artist = _FakeArtist(granular_tags_consolidated_at=None)
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(
        tasks, "build_global_tag_blocklist", lambda _s, redis_client=None: set()
    )
    consolidate_mock = MagicMock(return_value=["indie", "folk"])
    monkeypatch.setattr(tasks, "consolidate_artist_tags", consolidate_mock)

    result = tasks.consolidate_artist_tags_task.run(str(artist.id))

    assert result["status"] == "consolidated"
    assert result["tag_count"] == 2
    consolidate_mock.assert_called_once()


def test_task_reports_empty_when_no_tags_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)
    monkeypatch.setattr(
        tasks, "build_global_tag_blocklist", lambda _s, redis_client=None: set()
    )
    monkeypatch.setattr(
        tasks, "consolidate_artist_tags", lambda _s, _id, blocklist=None: []
    )

    result = tasks.consolidate_artist_tags_task.run(str(artist.id))

    assert result["status"] == "empty"
    assert result["tag_count"] == 0


def test_task_rolls_back_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    artist = _FakeArtist()
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.artists_repo, "get_artist_by_id", lambda _s, _u: artist)

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tasks, "build_global_tag_blocklist", boom)

    with pytest.raises(RuntimeError):
        tasks.consolidate_artist_tags_task.run(str(artist.id))

    session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# backfill_tag_consolidation — two-pass execution
# ---------------------------------------------------------------------------


def test_backfill_runs_two_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass 1 populates without blocklist, pass 2 applies the blocklist."""
    artists = [_FakeArtist(name=f"A{i}") for i in range(3)]
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_tag_consolidation",
        lambda _s, *, limit, force: artists,
    )

    blocklist_returned = {"too-broad-tag"}
    monkeypatch.setattr(
        tasks,
        "build_global_tag_blocklist",
        lambda _s, redis_client=None: blocklist_returned,
    )

    consolidate_calls: list[dict[str, Any]] = []

    def fake_consolidate(
        _s: Any, artist_id: Any, *, blocklist: Any = None
    ) -> list[str]:
        consolidate_calls.append({"artist_id": artist_id, "blocklist": blocklist})
        return ["indie folk"]

    monkeypatch.setattr(tasks, "consolidate_artist_tags", fake_consolidate)

    result = tasks.backfill_tag_consolidation.run()

    # Two passes over three artists = six calls.
    assert len(consolidate_calls) == 6
    pass1 = consolidate_calls[:3]
    pass2 = consolidate_calls[3:]
    assert all(c["blocklist"] is None for c in pass1)
    assert all(c["blocklist"] == blocklist_returned for c in pass2)

    assert result["pass1_processed"] == 3
    assert result["pass2_processed"] == 3
    assert result["with_tags"] == 3
    assert result["blocklist_size"] == len(blocklist_returned)


def test_backfill_caches_blocklist_in_redis(
    monkeypatch: pytest.MonkeyPatch, _patch_redis: _FakeRedis
) -> None:
    artists = [_FakeArtist()]
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_tag_consolidation",
        lambda _s, *, limit, force: artists,
    )
    monkeypatch.setattr(
        tasks,
        "build_global_tag_blocklist",
        lambda _s, redis_client=None: {"a", "b"},
    )
    monkeypatch.setattr(
        tasks, "consolidate_artist_tags", lambda _s, _id, blocklist=None: ["x"]
    )

    tasks.backfill_tag_consolidation.run()

    assert any(
        call[0] == "tag_consolidation:blocklist" for call in _patch_redis.set_calls
    )


def test_backfill_force_passes_force_to_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    def capture_list(_s: Any, *, limit: int, force: bool) -> list[Any]:
        received["force"] = force
        received["limit"] = limit
        return []

    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo, "list_artists_for_tag_consolidation", capture_list
    )
    monkeypatch.setattr(
        tasks, "build_global_tag_blocklist", lambda _s, redis_client=None: set()
    )

    tasks.backfill_tag_consolidation.run(force=True)

    assert received["force"] is True


def test_backfill_handles_zero_pending_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.artists_repo,
        "list_artists_for_tag_consolidation",
        lambda _s, *, limit, force: [],
    )
    monkeypatch.setattr(
        tasks, "build_global_tag_blocklist", lambda _s, redis_client=None: set()
    )
    consolidate_mock = MagicMock()
    monkeypatch.setattr(tasks, "consolidate_artist_tags", consolidate_mock)

    result = tasks.backfill_tag_consolidation.run()

    assert result["pass1_processed"] == 0
    assert result["pass2_processed"] == 0
    consolidate_mock.assert_not_called()
