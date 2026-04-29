"""Unit tests for the notification_preferences repository.

The repository is one thin layer above SQLAlchemy. The tests use a
MagicMock-backed session so coverage is exercised without touching
Postgres — the assertions check call shapes (``session.add``,
``session.flush``) and the return-value branches.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.data.models.notifications import NotificationPreferences
from backend.data.repositories import notification_preferences as prefs_repo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> MagicMock:
    """Return a MagicMock that mimics a SQLAlchemy session.

    Returns:
        A MagicMock session — no DB connection is opened.
    """
    return MagicMock(name="Session")


def _existing_prefs(user_id: uuid.UUID) -> NotificationPreferences:
    """Construct a NotificationPreferences instance the repo can hand back.

    Args:
        user_id: User UUID to attach to the row.

    Returns:
        A populated NotificationPreferences with default flags.
    """
    return NotificationPreferences(
        user_id=user_id,
        artist_announcements=True,
        venue_announcements=True,
        selling_fast_alerts=True,
        show_reminders=True,
        show_reminder_days_before=1,
        staff_picks=False,
        artist_spotlights=False,
        similar_artist_suggestions=False,
        weekly_digest=False,
        digest_day_of_week="monday",
        digest_hour=8,
        max_emails_per_week=3,
        quiet_hours_start=21,
        quiet_hours_end=8,
        timezone="America/New_York",
    )


# ---------------------------------------------------------------------------
# get_or_create_for_user
# ---------------------------------------------------------------------------


def test_get_or_create_returns_existing(session: MagicMock) -> None:
    """When a row exists, return it without inserting."""
    user_id = uuid.uuid4()
    existing = _existing_prefs(user_id)
    session.execute.return_value.scalar_one_or_none.return_value = existing

    result = prefs_repo.get_or_create_for_user(session, user_id)

    assert result is existing
    session.add.assert_not_called()


def test_get_or_create_inserts_when_missing(session: MagicMock) -> None:
    """When no row exists, create one with default flags and persist it."""
    user_id = uuid.uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = None

    result = prefs_repo.get_or_create_for_user(session, user_id)

    assert isinstance(result, NotificationPreferences)
    assert result.user_id == user_id
    session.add.assert_called_once_with(result)
    session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# update_preferences
# ---------------------------------------------------------------------------


def test_update_preferences_applies_attributes(session: MagicMock) -> None:
    """Patch dict is applied attribute-by-attribute and flushed."""
    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)

    updated = prefs_repo.update_preferences(
        session,
        prefs,
        weekly_digest=True,
        digest_hour=18,
        max_emails_per_week=7,
    )

    assert updated.weekly_digest is True
    assert updated.digest_hour == 18
    assert updated.max_emails_per_week == 7
    session.flush.assert_called_once()


def test_update_preferences_ignores_unknown_attributes(session: MagicMock) -> None:
    """Unknown kwargs do not raise but are skipped."""
    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)

    prefs_repo.update_preferences(session, prefs, totally_made_up_field=True)

    assert not hasattr(prefs, "totally_made_up_field")
    session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# pause_all / resume_all
# ---------------------------------------------------------------------------


def test_pause_all_snapshots_flags_and_stamps(session: MagicMock) -> None:
    """Pause snapshots every per-type flag and stamps paused_at."""
    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)
    prefs.weekly_digest = True
    prefs.staff_picks = True

    paused = prefs_repo.pause_all(session, prefs)

    assert paused.paused_at is not None
    snapshot: dict[str, Any] = paused.paused_snapshot or {}
    # Snapshot mirrors the per-type flags as they were before the pause.
    assert snapshot["weekly_digest"] is True
    assert snapshot["staff_picks"] is True
    assert snapshot["artist_announcements"] is True
    session.flush.assert_called_once()


def test_pause_all_is_idempotent_when_already_paused(session: MagicMock) -> None:
    """Calling pause twice does not overwrite the original snapshot."""
    from datetime import UTC, datetime

    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)
    prefs.paused_at = datetime(2026, 1, 1, tzinfo=UTC)
    prefs.paused_snapshot = {"weekly_digest": True}

    prefs_repo.pause_all(session, prefs)

    assert prefs.paused_snapshot == {"weekly_digest": True}
    assert prefs.paused_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_resume_all_restores_snapshot_and_clears_pause(session: MagicMock) -> None:
    """Resume copies the snapshot back and clears paused_at."""
    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)
    # Pre-condition: row was paused with a snapshot capturing prior state.
    from datetime import UTC, datetime

    prefs.paused_at = datetime(2026, 1, 1, tzinfo=UTC)
    prefs.paused_snapshot = {
        "artist_announcements": False,
        "venue_announcements": True,
        "weekly_digest": True,
    }

    resumed = prefs_repo.resume_all(session, prefs)

    assert resumed.paused_at is None
    assert resumed.paused_snapshot is None
    assert resumed.artist_announcements is False
    assert resumed.venue_announcements is True
    assert resumed.weekly_digest is True
    session.flush.assert_called_once()


def test_resume_all_no_op_when_not_paused(session: MagicMock) -> None:
    """Resuming an already-active row leaves it untouched and does not flush."""
    user_id = uuid.uuid4()
    prefs = _existing_prefs(user_id)
    prefs.paused_at = None
    prefs.paused_snapshot = None

    resumed = prefs_repo.resume_all(session, prefs)

    assert resumed is prefs
    assert resumed.paused_at is None
    session.flush.assert_not_called()
