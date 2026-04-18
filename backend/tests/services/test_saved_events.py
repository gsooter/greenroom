"""Unit tests for :mod:`backend.services.saved_events`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError
from backend.services import saved_events as saved_service


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _FakeEvent:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Show"
    slug: str = "show"
    starts_at: datetime = field(
        default_factory=lambda: datetime(2026, 5, 1, tzinfo=timezone.utc)
    )
    artists: list[str] = field(default_factory=list)
    image_url: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    status: Any = None
    venue: Any = None


@dataclass
class _FakeSaved:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    event: _FakeEvent = field(default_factory=_FakeEvent)
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 17, tzinfo=timezone.utc)
    )


def test_save_event_raises_when_event_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        saved_service.events_repo, "get_event_by_id", lambda _s, _i: None
    )
    with pytest.raises(NotFoundError):
        saved_service.save_event(
            MagicMock(), _FakeUser(), uuid.uuid4()  # type: ignore[arg-type]
        )


def test_save_event_returns_existing_if_already_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate saves are no-ops (idempotent)."""
    existing = _FakeSaved()
    monkeypatch.setattr(
        saved_service.events_repo,
        "get_event_by_id",
        lambda _s, _i: object(),
    )
    monkeypatch.setattr(
        saved_service.users_repo,
        "get_saved_event",
        lambda _s, _u, _e: existing,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(
        saved_service.users_repo, "create_saved_event", create_mock
    )
    result = saved_service.save_event(
        MagicMock(), _FakeUser(), uuid.uuid4()  # type: ignore[arg-type]
    )
    assert result is existing
    create_mock.assert_not_called()


def test_save_event_creates_when_not_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _FakeSaved()
    monkeypatch.setattr(
        saved_service.events_repo,
        "get_event_by_id",
        lambda _s, _i: object(),
    )
    monkeypatch.setattr(
        saved_service.users_repo,
        "get_saved_event",
        lambda _s, _u, _e: None,
    )
    monkeypatch.setattr(
        saved_service.users_repo,
        "create_saved_event",
        lambda _s, **_k: created,
    )
    assert (
        saved_service.save_event(
            MagicMock(), _FakeUser(), uuid.uuid4()  # type: ignore[arg-type]
        )
        is created
    )


def test_unsave_event_returns_false_when_nothing_to_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        saved_service.users_repo,
        "get_saved_event",
        lambda _s, _u, _e: None,
    )
    assert (
        saved_service.unsave_event(
            MagicMock(), _FakeUser(), uuid.uuid4()  # type: ignore[arg-type]
        )
        is False
    )


def test_unsave_event_deletes_and_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = _FakeSaved()
    monkeypatch.setattr(
        saved_service.users_repo,
        "get_saved_event",
        lambda _s, _u, _e: saved,
    )
    delete_mock = MagicMock()
    monkeypatch.setattr(
        saved_service.users_repo, "delete_saved_event", delete_mock
    )
    assert (
        saved_service.unsave_event(
            MagicMock(), _FakeUser(), uuid.uuid4()  # type: ignore[arg-type]
        )
        is True
    )
    delete_mock.assert_called_once()


def test_list_saved_events_forwards_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(
        _session: Any, _uid: uuid.UUID, *, page: int, per_page: int
    ) -> tuple[list[Any], int]:
        captured["page"] = page
        captured["per_page"] = per_page
        return [], 0

    monkeypatch.setattr(
        saved_service.users_repo, "list_saved_events", fake_list
    )
    saved_service.list_saved_events(
        MagicMock(), _FakeUser(), page=2, per_page=5  # type: ignore[arg-type]
    )
    assert captured == {"page": 2, "per_page": 5}


def test_serialize_saved_event_embeds_event_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saved event payload carries saved_at + the canonical event summary."""
    saved = _FakeSaved()
    monkeypatch.setattr(
        saved_service.events_service,
        "serialize_event_summary",
        lambda _event: {"id": "summary"},
    )
    payload = saved_service.serialize_saved_event(saved)  # type: ignore[arg-type]
    assert payload["saved_at"] == saved.created_at.isoformat()
    assert payload["event"] == {"id": "summary"}
