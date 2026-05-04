"""Tests for :mod:`backend.services.artist_hydration_tasks`."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import artist_hydration_tasks
from backend.services.artist_hydration import MassHydrationResult


def test_mass_hydrate_task_delegates_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task is a thin wrapper around the service function."""
    session_mock = MagicMock()
    factory_mock = MagicMock()
    factory_mock.return_value.__enter__.return_value = session_mock
    factory_mock.return_value.__exit__.return_value = None
    monkeypatch.setattr(
        artist_hydration_tasks, "get_session_factory", lambda: factory_mock
    )

    captured: dict[str, Any] = {}

    def fake_mass_hydrate(_session: Any, *, admin_email: str) -> MassHydrationResult:
        captured["admin_email"] = admin_email
        return MassHydrationResult(
            sources_processed=2,
            sources_skipped=1,
            artists_added=7,
            daily_cap_reached=False,
            audit_log_ids=[],
            per_source=[
                {"artist_id": "abc", "artist_name": "Caamp", "added_count": 4},
                {
                    "artist_id": "def",
                    "artist_name": "Phoebe Bridgers",
                    "added_count": 3,
                },
            ],
        )

    monkeypatch.setattr(
        "backend.services.artist_hydration.mass_hydrate", fake_mass_hydrate
    )

    payload = artist_hydration_tasks.mass_hydrate_task("ops@greenroom.test")

    assert captured["admin_email"] == "ops@greenroom.test"
    assert payload["sources_processed"] == 2
    assert payload["artists_added"] == 7
    assert payload["per_source"][0]["artist_name"] == "Caamp"


def test_mass_hydrate_task_default_email_is_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The beat-scheduled invocation passes no args; the default kicks in."""
    session_mock = MagicMock()
    factory_mock = MagicMock()
    factory_mock.return_value.__enter__.return_value = session_mock
    factory_mock.return_value.__exit__.return_value = None
    monkeypatch.setattr(
        artist_hydration_tasks, "get_session_factory", lambda: factory_mock
    )

    captured: dict[str, Any] = {}

    def fake_mass_hydrate(_session: Any, *, admin_email: str) -> MassHydrationResult:
        captured["admin_email"] = admin_email
        return MassHydrationResult()

    monkeypatch.setattr(
        "backend.services.artist_hydration.mass_hydrate", fake_mass_hydrate
    )

    artist_hydration_tasks.mass_hydrate_task()

    assert captured["admin_email"] == artist_hydration_tasks.NIGHTLY_OPERATOR_EMAIL


def test_mass_hydrate_task_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service failure surfaces as a task failure (after rollback)."""
    session_mock = MagicMock()
    factory_mock = MagicMock()
    factory_mock.return_value.__enter__.return_value = session_mock
    factory_mock.return_value.__exit__.return_value = None
    monkeypatch.setattr(
        artist_hydration_tasks, "get_session_factory", lambda: factory_mock
    )

    def boom(_session: Any, *, admin_email: str) -> MassHydrationResult:
        raise RuntimeError("upstream broke")

    monkeypatch.setattr("backend.services.artist_hydration.mass_hydrate", boom)

    with pytest.raises(RuntimeError, match="upstream broke"):
        artist_hydration_tasks.mass_hydrate_task()
    session_mock.rollback.assert_called_once()


def test_beat_schedule_contains_nightly_mass_hydrate() -> None:
    """The beat-schedule entry must be present and aimed at 03:00 ET."""
    from backend.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "mass-hydrate-artist-catalog-nightly" in schedule
    entry = schedule["mass-hydrate-artist-catalog-nightly"]
    assert entry["task"] == "backend.services.artist_hydration_tasks.mass_hydrate_task"
    cron = entry["schedule"]
    # crontab fields are stored as set-of-allowed-values
    assert 3 in cron.hour
    assert 0 in cron.minute
