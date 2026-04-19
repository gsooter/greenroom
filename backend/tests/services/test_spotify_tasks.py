"""Unit tests for :mod:`backend.services.spotify_tasks`.

The Celery task is a thin wrapper that owns a DB session. Tests replace
the session factory and the repository/sync calls to exercise the three
branches: user missing, user inactive, happy path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from backend.services import spotify_tasks


@dataclass
class _FakeUser:
    is_active: bool = True


class _FakeSession:
    """Context-manager-compatible fake that records commit/rollback calls."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.closed = False

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True


def test_sync_task_noop_when_user_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing user row returns synced=0 without hitting Spotify."""
    session = _FakeSession()
    monkeypatch.setattr(spotify_tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        spotify_tasks.users_repo, "get_user_by_id", lambda _s, _uid: None
    )
    sync_mock = MagicMock()
    monkeypatch.setattr(spotify_tasks.spotify_service, "sync_top_artists", sync_mock)
    result = spotify_tasks.sync_user_spotify_data(str(uuid.uuid4()))
    assert result["synced"] == 0
    sync_mock.assert_not_called()


def test_sync_task_skips_inactive_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(spotify_tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        spotify_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: _FakeUser(is_active=False),
    )
    sync_mock = MagicMock()
    monkeypatch.setattr(spotify_tasks.spotify_service, "sync_top_artists", sync_mock)
    result = spotify_tasks.sync_user_spotify_data(str(uuid.uuid4()))
    assert result["synced"] == 0
    sync_mock.assert_not_called()


def test_sync_task_commits_and_returns_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(spotify_tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        spotify_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: _FakeUser(),
    )
    monkeypatch.setattr(
        spotify_tasks.spotify_service,
        "sync_top_artists",
        lambda _s, _u: 17,
    )
    result = spotify_tasks.sync_user_spotify_data(str(uuid.uuid4()))
    assert result["synced"] == 17
    session.commit.assert_called_once()


def test_sync_task_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(spotify_tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        spotify_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: _FakeUser(),
    )

    def boom(_s: object, _u: object) -> int:
        raise RuntimeError("spotify down")

    monkeypatch.setattr(spotify_tasks.spotify_service, "sync_top_artists", boom)
    with pytest.raises(RuntimeError):
        spotify_tasks.sync_user_spotify_data(str(uuid.uuid4()))
    session.rollback.assert_called_once()
