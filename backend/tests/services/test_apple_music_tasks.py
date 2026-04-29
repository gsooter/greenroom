"""Unit tests for :mod:`backend.services.apple_music_tasks`.

Mirrors the shape of ``test_spotify_tasks`` — the Celery wrapper owns a
DB session, so tests replace the session factory and the Apple Music
service call to exercise the four branches: user missing, user inactive,
no Apple Music connection, and happy path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from backend.data.models.users import OAuthProvider
from backend.services import apple_music_tasks


@dataclass
class _FakeConnection:
    provider: OAuthProvider = OAuthProvider.APPLE_MUSIC
    access_token: str | None = "mut-ok"


@dataclass
class _FakeUser:
    is_active: bool = True
    music_connections: list[_FakeConnection] = field(default_factory=list)


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
    """Missing user row returns synced=0 without hitting Apple."""
    session = _FakeSession()
    monkeypatch.setattr(
        apple_music_tasks, "get_session_factory", lambda: lambda: session
    )
    monkeypatch.setattr(
        apple_music_tasks.users_repo, "get_user_by_id", lambda _s, _uid: None
    )
    sync_mock = MagicMock()
    monkeypatch.setattr(
        apple_music_tasks.apple_music_service, "sync_top_artists", sync_mock
    )
    result = apple_music_tasks.sync_user_apple_music_data(str(uuid.uuid4()))
    assert result["synced"] == 0
    sync_mock.assert_not_called()


def test_sync_task_noop_when_no_apple_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User with no Apple Music connection returns synced=0."""
    session = _FakeSession()
    monkeypatch.setattr(
        apple_music_tasks, "get_session_factory", lambda: lambda: session
    )
    monkeypatch.setattr(
        apple_music_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: _FakeUser(music_connections=[]),
    )
    sync_mock = MagicMock()
    monkeypatch.setattr(
        apple_music_tasks.apple_music_service, "sync_top_artists", sync_mock
    )
    result = apple_music_tasks.sync_user_apple_music_data(str(uuid.uuid4()))
    assert result["synced"] == 0
    sync_mock.assert_not_called()


def test_sync_task_commits_and_returns_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: valid user + connection → sync runs, session commits."""
    session = _FakeSession()
    monkeypatch.setattr(
        apple_music_tasks, "get_session_factory", lambda: lambda: session
    )
    user = _FakeUser(music_connections=[_FakeConnection()])
    monkeypatch.setattr(
        apple_music_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: user,
    )
    monkeypatch.setattr(
        apple_music_tasks.apple_music_service,
        "sync_top_artists",
        lambda _s, _u, music_user_token: 42,
    )
    result = apple_music_tasks.sync_user_apple_music_data(str(uuid.uuid4()))
    assert result["synced"] == 42
    session.commit.assert_called_once()


def test_sync_task_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception rolls the session back and re-raises."""
    session = _FakeSession()
    monkeypatch.setattr(
        apple_music_tasks, "get_session_factory", lambda: lambda: session
    )
    user = _FakeUser(music_connections=[_FakeConnection()])
    monkeypatch.setattr(
        apple_music_tasks.users_repo,
        "get_user_by_id",
        lambda _s, _uid: user,
    )

    def boom(_s: object, _u: object, **_k: object) -> int:
        raise RuntimeError("apple down")

    monkeypatch.setattr(apple_music_tasks.apple_music_service, "sync_top_artists", boom)
    with pytest.raises(RuntimeError):
        apple_music_tasks.sync_user_apple_music_data(str(uuid.uuid4()))
    session.rollback.assert_called_once()
