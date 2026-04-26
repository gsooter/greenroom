"""Tests for :func:`dispatch_weekly_digests`.

The dispatcher is the hourly fan-out: it queries every active
weekly-digest subscriber, filters by per-user timezone (so a user in
Pacific time gets fired at 08:00 PST not 08:00 UTC), and hands off
each due user to the per-user send pipeline. Tests pin:

* The dispatcher only enqueues users whose local weekday/hour matches
  their stored ``digest_day_of_week`` / ``digest_hour``.
* Quiet-hours users are skipped without invoking ``send_fn``.
* The dispatcher reports a summary dict of counters so the Celery
  task wrapper can log a single structured line per run.
* A per-user ``send_fn`` exception does not abort the rest of the run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import notifications


def _prefs(
    *,
    user_id: uuid.UUID | None = None,
    weekly_digest: bool = True,
    paused_at: datetime | None = None,
    timezone: str = "America/New_York",
    digest_day_of_week: str = "sunday",
    digest_hour: int = 8,
    quiet_hours_start: int = 21,
    quiet_hours_end: int = 8,
    max_emails_per_week: int | None = 3,
) -> MagicMock:
    """Build a MagicMock NotificationPreferences row for the dispatcher."""
    prefs = MagicMock(name="NotificationPreferences")
    prefs.user_id = user_id or uuid.uuid4()
    prefs.weekly_digest = weekly_digest
    prefs.paused_at = paused_at
    prefs.timezone = timezone
    prefs.digest_day_of_week = digest_day_of_week
    prefs.digest_hour = digest_hour
    prefs.quiet_hours_start = quiet_hours_start
    prefs.quiet_hours_end = quiet_hours_end
    prefs.max_emails_per_week = max_emails_per_week
    return prefs


def test_dispatch_only_sends_to_users_due_in_their_local_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pacific user at 08:00 PST is sent; an Eastern user at 11:00 EST is not."""
    # 2026-04-26 is a Sunday. 15:00 UTC == 11:00 EST == 08:00 PST.
    now = datetime(2026, 4, 26, 15, 0, tzinfo=UTC)
    pacific_user = _prefs(
        timezone="America/Los_Angeles",
        digest_day_of_week="sunday",
        digest_hour=8,
    )
    eastern_user = _prefs(
        timezone="America/New_York",
        digest_day_of_week="sunday",
        digest_hour=8,
    )
    monkeypatch.setattr(
        notifications.prefs_repo,
        "list_active_weekly_digest_subscribers",
        lambda *_a, **_k: [pacific_user, eastern_user],
    )

    sent: list[uuid.UUID] = []

    def fake_send(_session: Any, user_id: uuid.UUID, *, now: datetime) -> bool:
        sent.append(user_id)
        return True

    summary = notifications.dispatch_weekly_digests(
        MagicMock(), now=now, send_fn=fake_send
    )
    assert sent == [pacific_user.user_id]
    assert summary["candidates"] == 2
    assert summary["sent"] == 1
    assert summary["skipped_not_due"] == 1


def test_dispatch_skips_users_in_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose digest hour falls inside their quiet window is skipped."""
    # 2026-04-26 Sunday. 08:00 ET — but this user has set quiet hours
    # to 06..10, so 08:00 falls inside it. They should not be sent to.
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)  # 08:00 ET
    quiet_user = _prefs(
        digest_day_of_week="sunday",
        digest_hour=8,
        quiet_hours_start=6,
        quiet_hours_end=10,
    )
    monkeypatch.setattr(
        notifications.prefs_repo,
        "list_active_weekly_digest_subscribers",
        lambda *_a, **_k: [quiet_user],
    )

    sent: list[uuid.UUID] = []

    def fake_send(_session: Any, user_id: uuid.UUID, *, now: datetime) -> bool:
        sent.append(user_id)
        return True

    summary = notifications.dispatch_weekly_digests(
        MagicMock(), now=now, send_fn=fake_send
    )
    assert sent == []
    assert summary["skipped_quiet_hours"] == 1


def test_dispatch_continues_when_send_fn_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One user's send failure does not abort the rest of the run."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)  # 08:00 ET, Sunday
    bad_user = _prefs()
    good_user = _prefs()
    monkeypatch.setattr(
        notifications.prefs_repo,
        "list_active_weekly_digest_subscribers",
        lambda *_a, **_k: [bad_user, good_user],
    )

    calls: list[uuid.UUID] = []

    def fake_send(_session: Any, user_id: uuid.UUID, *, now: datetime) -> bool:
        calls.append(user_id)
        if user_id == bad_user.user_id:
            raise RuntimeError("boom")
        return True

    summary = notifications.dispatch_weekly_digests(
        MagicMock(), now=now, send_fn=fake_send
    )
    assert calls == [bad_user.user_id, good_user.user_id]
    assert summary["sent"] == 1
    assert summary["errors"] == 1


def test_dispatch_counts_send_fn_returning_false_as_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A False return from ``send_fn`` is a skip (cap/idempotency), not an error."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)  # 08:00 ET, Sunday
    user = _prefs()
    monkeypatch.setattr(
        notifications.prefs_repo,
        "list_active_weekly_digest_subscribers",
        lambda *_a, **_k: [user],
    )

    summary = notifications.dispatch_weekly_digests(
        MagicMock(),
        now=now,
        send_fn=lambda *_a, **_k: False,
    )
    assert summary["sent"] == 0
    assert summary["skipped_send_returned_false"] == 1


def test_dispatch_with_no_subscribers_returns_zero_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty subscriber list returns a zeroed-out summary, no crashes."""
    monkeypatch.setattr(
        notifications.prefs_repo,
        "list_active_weekly_digest_subscribers",
        lambda *_a, **_k: [],
    )
    sent: list[Any] = []
    summary = notifications.dispatch_weekly_digests(
        MagicMock(),
        now=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        send_fn=lambda *_a, **_k: sent.append(_a) or True,
    )
    assert sent == []
    assert summary["candidates"] == 0
    assert summary["sent"] == 0
