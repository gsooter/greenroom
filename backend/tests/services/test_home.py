"""Unit tests for :mod:`backend.services.home`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import home as home_service


@dataclass
class _FakeUser:
    """Subset of the User model the home service reads."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    city_id: uuid.UUID | None = None
    last_home_visit_at: datetime | None = None
    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None
    tidal_top_artists: list[dict[str, Any]] | None = None
    apple_top_artists: list[dict[str, Any]] | None = None


def test_collect_anchor_display_names_unions_every_source() -> None:
    """Every cached snapshot contributes names; whitespace is trimmed; dedupe wins."""
    user = _FakeUser(
        spotify_top_artists=[{"name": "Phoebe Bridgers"}, {"name": " Soccer Mommy "}],
        spotify_recent_artists=[{"name": "Phoebe Bridgers"}, {"name": "Boygenius"}],
        tidal_top_artists=[{"name": "Big Thief"}],
        apple_top_artists=[{"name": "Indigo De Souza"}, {"missing": "name"}],
    )
    names = home_service._collect_anchor_display_names(user)  # type: ignore[arg-type]
    assert names == {
        "Phoebe Bridgers",
        "Soccer Mommy",
        "Boygenius",
        "Big Thief",
        "Indigo De Souza",
    }


def test_collect_anchor_display_names_empty_for_blank_user() -> None:
    """A user with no music-service snapshots yields an empty set."""
    assert home_service._collect_anchor_display_names(_FakeUser()) == set()  # type: ignore[arg-type]


def test_resolve_window_start_uses_last_visit_when_set() -> None:
    """A returning user's window starts at their previous visit timestamp."""
    last = datetime(2026, 5, 1, 12, tzinfo=UTC)
    user = _FakeUser(last_home_visit_at=last)
    now = datetime(2026, 5, 3, 12, tzinfo=UTC)
    assert home_service._resolve_window_start(user, now) == last  # type: ignore[arg-type]


def test_resolve_window_start_falls_back_to_30_days_when_null() -> None:
    """First-time users get a fixed 30-day fallback so the section isn't empty."""
    now = datetime(2026, 5, 3, 12, tzinfo=UTC)
    user = _FakeUser(last_home_visit_at=None)
    assert home_service._resolve_window_start(user, now) == now - timedelta(days=30)  # type: ignore[arg-type]


def test_get_new_since_last_visit_short_circuits_when_no_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No anchor names means no meaningful "new since" definition — return []."""
    session = MagicMock()
    user = _FakeUser()
    # Guard: if the function tried to query, the test would catch it via the
    # mock execute that returns nothing useful.
    result = home_service.get_new_since_last_visit(session, user)  # type: ignore[arg-type]
    assert result == []
    session.execute.assert_not_called()


def test_get_new_since_last_visit_executes_select_when_anchors_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anchor names cause the function to issue a single SELECT."""
    session = MagicMock()
    fake_event = object()
    scalars = MagicMock()
    scalars.all.return_value = [fake_event]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    session.execute.return_value = execute_result

    user = _FakeUser(spotify_top_artists=[{"name": "Phoebe Bridgers"}])
    monkeypatch.setattr(
        home_service.regions_repo,
        "get_region_for_city",
        lambda _s, _cid: None,
    )

    result = home_service.get_new_since_last_visit(
        session,
        user,
        now=datetime(2026, 5, 3, 12, tzinfo=UTC),  # type: ignore[arg-type]
    )
    assert result == [fake_event]
    session.execute.assert_called_once()


def test_has_signal_true_when_music_service_cached() -> None:
    """A connected service alone is enough — no follow query required."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"name": "Phoebe"}])
    assert home_service.has_signal(session, user) is True  # type: ignore[arg-type]
    session.execute.assert_not_called()


def test_has_signal_true_when_three_or_more_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No music service but ≥3 follows still flips the gate to True."""
    session = MagicMock()
    user = _FakeUser()

    # 2 artist follows + 1 venue follow = 3
    session.execute.return_value.scalar_one.side_effect = [2, 1]

    assert home_service.has_signal(session, user) is True  # type: ignore[arg-type]


def test_has_signal_false_when_under_three_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two follows total isn't enough — the home page should show the welcome prompt."""
    session = MagicMock()
    user = _FakeUser()

    session.execute.return_value.scalar_one.side_effect = [1, 1]

    assert home_service.has_signal(session, user) is False  # type: ignore[arg-type]


def test_update_last_home_visit_at_writes_now_when_user_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async update sets the column to the supplied timestamp."""
    session = MagicMock()
    user = _FakeUser()
    monkeypatch.setattr(home_service.users_repo, "get_user_by_id", lambda _s, _u: user)

    pinned = datetime(2026, 5, 3, 18, tzinfo=UTC)
    home_service.update_last_home_visit_at(session, user.id, now=pinned)
    assert user.last_home_visit_at == pinned
    session.flush.assert_called_once()


def test_update_last_home_visit_at_silently_no_ops_for_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted user can't be updated — but the task must not blow up."""
    session = MagicMock()
    monkeypatch.setattr(home_service.users_repo, "get_user_by_id", lambda _s, _u: None)
    home_service.update_last_home_visit_at(session, uuid.uuid4())
    session.flush.assert_not_called()
