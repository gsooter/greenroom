"""Unit tests for the unified notification dispatcher.

Each test stubs out the repositories the dispatcher consults so the
routing rules and quiet-hour math are exercised in isolation. The
real database is intentionally not part of these tests; integration
coverage will land alongside the Postgres-backed test database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.services import notification_dispatcher
from backend.services.notification_dispatcher import (
    NotificationTrigger,
    NotificationType,
)
from backend.services.push import PushPayload, SendResult


@dataclass
class _FakeUser:
    id: uuid.UUID
    email_bounced_at: Any = None


@dataclass
class _FakePrefs:
    artist_announcements: bool = True
    venue_announcements: bool = True
    show_reminders: bool = True
    selling_fast_alerts: bool = True
    weekly_digest: bool = True
    paused_at: Any = None
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8
    timezone: str = "America/New_York"


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: _FakeUser | None,
    prefs: _FakePrefs,
    subs: list[Any] | None = None,
    recent_pushes: int = 0,
    claim_returns: bool = True,
) -> dict[str, Any]:
    """Wire up minimal stubs for every repository the dispatcher hits.

    Returns a dict the test can read to make assertions about the
    side effects (e.g., what payload was claimed in the log).
    """
    state: dict[str, Any] = {
        "claims": [],
        "push_send_calls": [],
    }

    monkeypatch.setattr(
        "backend.data.repositories.users.get_user_by_id",
        lambda _session, _user_id: user,
    )
    monkeypatch.setattr(
        "backend.data.repositories.notification_preferences.get_or_create_for_user",
        lambda _session, _user_id: prefs,
    )
    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.list_active_for_user",
        lambda _session, _user_id: subs or [],
    )
    monkeypatch.setattr(
        "backend.data.repositories.notification_log.count_recent_pushes",
        lambda *_args, **_kwargs: recent_pushes,
    )

    def _claim(_session: Any, **kwargs: Any) -> bool:
        state["claims"].append(kwargs)
        return claim_returns

    monkeypatch.setattr(
        "backend.data.repositories.notification_log.claim",
        _claim,
    )

    def _send(_session: Any, _user_id: uuid.UUID, payload: PushPayload) -> SendResult:
        state["push_send_calls"].append(payload)
        return SendResult(attempted=1, succeeded=1, disabled=0, skipped_no_vapid=False)

    state["push_sender"] = _send
    return state


def test_tour_announcement_routes_to_push_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(),
        subs=[object()],
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key=str(uuid.uuid4()),
        payload={
            "performer_name": "Phoebe Bridgers",
            "venue_name": "Capital One Arena",
            "url": "https://greenroom.test/events/x",
        },
        # 2 PM ET — outside quiet hours.
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )

    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "sent"
    assert result.email is None
    assert len(state["claims"]) == 1
    assert state["claims"][0]["channel"] == "push"


def test_tour_announcement_skipped_when_pref_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(artist_announcements=False),
        subs=[object()],
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )

    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "skipped:prefs:artist_announcements"
    assert state["push_send_calls"] == []


def test_dispatch_returns_queued_during_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(),
        subs=[object()],
    )

    # 03:00 UTC = 23:00 ET previous day; user quiet hours 21..8.
    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 4, 3, 0, tzinfo=UTC),
    )
    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "queued"
    assert result.queued_until is not None
    assert state["push_send_calls"] == []


def test_dispatch_skips_paused_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    paused = _FakePrefs(paused_at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC))
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=paused,
        subs=[object()],
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )
    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "skipped:paused"
    assert state["push_send_calls"] == []


def test_dispatch_rate_limits_push_at_five_per_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(),
        subs=[object()],
        recent_pushes=5,
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )
    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "skipped:rate_limited"
    assert state["push_send_calls"] == []


def test_dispatch_dedupes_via_log(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(),
        subs=[object()],
        claim_returns=False,
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )
    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "skipped:duplicate"
    assert state["push_send_calls"] == []


def test_dispatch_skips_push_when_no_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(id=uuid.uuid4())
    state = _patch_repos(
        monkeypatch,
        user=user,
        prefs=_FakePrefs(),
        subs=[],
    )

    trigger = NotificationTrigger(
        user_id=user.id,
        notification_type=NotificationType.TOUR_ANNOUNCEMENT,
        dedupe_key="dk",
        payload={"performer_name": "x", "venue_name": "y", "url": "/"},
        trigger_time=datetime(2026, 5, 3, 18, 0, tzinfo=UTC),
    )
    result = notification_dispatcher.dispatch(
        session=None,  # type: ignore[arg-type]
        trigger=trigger,
        push_sender=state["push_sender"],
    )
    assert result.push == "skipped:no_subscriptions"


def test_dispatch_unknown_type_raises() -> None:
    trigger = NotificationTrigger(
        user_id=uuid.uuid4(),
        notification_type="nonexistent",  # type: ignore[arg-type]
        dedupe_key="dk",
    )
    with pytest.raises(ValueError):
        notification_dispatcher.dispatch(
            session=None,  # type: ignore[arg-type]
            trigger=trigger,
        )


def test_render_show_reminder_payload() -> None:
    trigger = NotificationTrigger(
        user_id=uuid.uuid4(),
        notification_type=NotificationType.SHOW_REMINDER_24H,
        dedupe_key="dk",
        payload={
            "performer_name": "Phoebe Bridgers",
            "venue_name": "Capital One Arena",
            "doors_label": "7:00 PM",
            "url": "https://greenroom.test/events/x",
        },
    )
    payload = notification_dispatcher._render_push(trigger)
    assert payload is not None
    assert "Tomorrow" in payload.title
    assert "Capital One Arena" in payload.body
    assert "Doors 7:00 PM" in payload.body
    assert payload.tag and payload.tag.startswith("reminder24:")
