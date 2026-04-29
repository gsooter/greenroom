"""Tests for the schedule guards in :mod:`backend.services.notifications`.

These three predicates gate every scheduled email send:

* :func:`is_in_quiet_hours` — is the recipient's local time inside
  their configured quiet window?
* :func:`is_due_for_weekly_digest` — is now the day-of-week and
  hour-of-day the user picked for their weekly digest?
* :func:`is_at_weekly_cap` — has the user already received their
  configured ``max_emails_per_week`` quota in the trailing 7 days?

The hourly dispatcher consults all three before fanning out a digest
send. Each guard is pure (no DB calls except the cap check), so the
tests exercise them with a stand-in :class:`NotificationPreferences`
shape rather than real ORM rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import notifications


@dataclass
class _Prefs:
    """Stand-in for :class:`NotificationPreferences` in guard tests."""

    weekly_digest: bool = True
    digest_day_of_week: str = "monday"
    digest_hour: int = 8
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8
    timezone: str = "America/New_York"
    max_emails_per_week: int | None = 3
    paused_at: datetime | None = None


# ---------------------------------------------------------------------------
# is_in_quiet_hours
# ---------------------------------------------------------------------------


def test_quiet_hours_true_inside_overnight_window() -> None:
    """22:00 in the user's tz with a 21..8 window → quiet."""
    prefs = _Prefs(quiet_hours_start=21, quiet_hours_end=8, timezone="America/New_York")
    # 02:00 UTC on a winter day is 21:00 EST.
    now = datetime(2026, 1, 5, 2, 0, tzinfo=UTC)
    assert notifications.is_in_quiet_hours(prefs, now) is True


def test_quiet_hours_false_outside_overnight_window() -> None:
    """14:00 in the user's tz with a 21..8 window → not quiet."""
    prefs = _Prefs(quiet_hours_start=21, quiet_hours_end=8, timezone="America/New_York")
    # 19:00 UTC on a winter day is 14:00 EST.
    now = datetime(2026, 1, 5, 19, 0, tzinfo=UTC)
    assert notifications.is_in_quiet_hours(prefs, now) is False


def test_quiet_hours_handles_same_day_window() -> None:
    """A non-wrapping 13..17 window is quiet at 14, not at 18."""
    prefs = _Prefs(
        quiet_hours_start=13, quiet_hours_end=17, timezone="America/New_York"
    )
    in_window = datetime(2026, 1, 5, 19, 0, tzinfo=UTC)  # 14:00 EST
    out_of_window = datetime(2026, 1, 5, 23, 0, tzinfo=UTC)  # 18:00 EST
    assert notifications.is_in_quiet_hours(prefs, in_window) is True
    assert notifications.is_in_quiet_hours(prefs, out_of_window) is False


def test_quiet_hours_excludes_end_hour() -> None:
    """The end hour itself is the wake-up hour, not a quiet hour."""
    prefs = _Prefs(quiet_hours_start=21, quiet_hours_end=8, timezone="America/New_York")
    # 13:00 UTC is 08:00 EST.
    now = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    assert notifications.is_in_quiet_hours(prefs, now) is False


# ---------------------------------------------------------------------------
# is_due_for_weekly_digest
# ---------------------------------------------------------------------------


def test_due_when_weekday_and_hour_match_in_user_tz() -> None:
    """Monday 08:00 EST with a Monday/8 prefs row is due."""
    prefs = _Prefs(digest_day_of_week="monday", digest_hour=8)
    # 2026-01-05 13:00 UTC = Monday 08:00 EST.
    now = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    assert notifications.is_due_for_weekly_digest(prefs, now) is True


def test_not_due_when_weekday_does_not_match() -> None:
    """Tuesday 08:00 EST with Monday/8 prefs is not due."""
    prefs = _Prefs(digest_day_of_week="monday", digest_hour=8)
    now = datetime(2026, 1, 6, 13, 0, tzinfo=UTC)  # Tuesday 08:00 EST
    assert notifications.is_due_for_weekly_digest(prefs, now) is False


def test_not_due_when_hour_does_not_match() -> None:
    """Monday 09:00 EST with Monday/8 prefs is not due."""
    prefs = _Prefs(digest_day_of_week="monday", digest_hour=8)
    now = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # Monday 09:00 EST
    assert notifications.is_due_for_weekly_digest(prefs, now) is False


def test_not_due_when_digest_disabled() -> None:
    """A weekly_digest=False row is never due even at the right time."""
    prefs = _Prefs(weekly_digest=False, digest_day_of_week="monday", digest_hour=8)
    now = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    assert notifications.is_due_for_weekly_digest(prefs, now) is False


def test_not_due_when_globally_paused() -> None:
    """A paused row is never due."""
    prefs = _Prefs(
        digest_day_of_week="monday",
        digest_hour=8,
        paused_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    now = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    assert notifications.is_due_for_weekly_digest(prefs, now) is False


def test_due_respects_user_timezone_for_day_boundary() -> None:
    """A user in Pacific time is "Sunday" while UTC says Monday.

    A user with digest_day_of_week='sunday' / hour=23 in
    America/Los_Angeles should be due at 2026-01-05 07:00 UTC, which
    is Sunday 23:00 PST. The naive UTC weekday would be Monday — the
    guard must localise first.
    """
    prefs = _Prefs(
        digest_day_of_week="sunday",
        digest_hour=23,
        timezone="America/Los_Angeles",
    )
    now = datetime(2026, 1, 5, 7, 0, tzinfo=UTC)
    assert notifications.is_due_for_weekly_digest(prefs, now) is True


# ---------------------------------------------------------------------------
# is_at_weekly_cap
# ---------------------------------------------------------------------------


def test_cap_false_when_max_is_none() -> None:
    """``max_emails_per_week=None`` means unlimited — never at cap."""
    prefs = _Prefs(max_emails_per_week=None)
    session = MagicMock()
    user_id = uuid.uuid4()
    assert (
        notifications.is_at_weekly_cap(session, user_id, prefs, datetime.now(UTC))
        is False
    )
    # No DB lookup needed for the unlimited case.
    session.execute.assert_not_called()


def test_cap_false_below_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 3/week user with 2 sends in the last week is not at cap."""
    prefs = _Prefs(max_emails_per_week=3)
    user_id = uuid.uuid4()

    def fake_count(_session: Any, uid: uuid.UUID, since: datetime) -> int:
        assert uid == user_id
        # since must be ~7 days before now.
        assert datetime.now(UTC) - since >= timedelta(days=6, hours=23)
        return 2

    monkeypatch.setattr(
        notifications.digest_log_repo, "count_recent_for_user", fake_count
    )
    assert (
        notifications.is_at_weekly_cap(MagicMock(), user_id, prefs, datetime.now(UTC))
        is False
    )


def test_cap_true_at_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 3/week user with 3 sends in the last week is at cap."""
    prefs = _Prefs(max_emails_per_week=3)

    monkeypatch.setattr(
        notifications.digest_log_repo,
        "count_recent_for_user",
        lambda *_a, **_k: 3,
    )
    assert (
        notifications.is_at_weekly_cap(
            MagicMock(), uuid.uuid4(), prefs, datetime.now(UTC)
        )
        is True
    )


def test_cap_window_is_exactly_seven_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trailing window must be exactly 7 days back from ``now``."""
    prefs = _Prefs(max_emails_per_week=1)
    captured: dict[str, Any] = {}
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    def fake_count(_session: Any, _uid: uuid.UUID, since: datetime) -> int:
        captured["since"] = since
        return 0

    monkeypatch.setattr(
        notifications.digest_log_repo, "count_recent_for_user", fake_count
    )
    notifications.is_at_weekly_cap(MagicMock(), uuid.uuid4(), prefs, now)
    assert captured["since"] == now - timedelta(days=7)
