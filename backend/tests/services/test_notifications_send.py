"""Tests for :func:`send_weekly_digest_to_user`.

The send function is the join point between the dispatcher and the
email pipeline. It re-checks the idempotency and cap guards inside
the per-user task (so a stale dispatcher snapshot can't double-send),
asks the assembler for content, hands off to ``compose_email``, and
writes the ``email_digest_log`` row that future cap and idempotency
checks read.

Tests pin five contracts:

* No content → no email and no log row.
* Already-sent in the trailing 6 days → skip without re-sending.
* Cap-reached → skip without re-sending.
* Happy path → ``compose_email`` is invoked with the assembled
  context and a log row is written.
* Compose failure → log row is *not* written (so a transient Resend
  outage doesn't burn the user's weekly slot).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.services import notifications


def _user(**overrides: Any) -> MagicMock:
    user = MagicMock(name="User")
    user.id = overrides.pop("id", uuid.uuid4())
    user.email = overrides.pop("email", "user@example.test")
    user.display_name = overrides.pop("display_name", "Riley")
    user.city_id = overrides.pop("city_id", uuid.uuid4())
    user.spotify_top_artist_ids = overrides.pop("spotify_top_artist_ids", None)
    user.spotify_recent_artist_ids = overrides.pop("spotify_recent_artist_ids", None)
    return user


def _prefs(**overrides: Any) -> MagicMock:
    prefs = MagicMock(name="NotificationPreferences")
    prefs.weekly_digest = overrides.pop("weekly_digest", True)
    prefs.timezone = overrides.pop("timezone", "America/New_York")
    prefs.max_emails_per_week = overrides.pop("max_emails_per_week", 3)
    prefs.paused_at = overrides.pop("paused_at", None)
    prefs.quiet_hours_start = overrides.pop("quiet_hours_start", 21)
    prefs.quiet_hours_end = overrides.pop("quiet_hours_end", 8)
    return prefs


def _digest() -> notifications.WeeklyDigest:
    return notifications.WeeklyDigest(
        heading="Your week ahead",
        intro="Here are the shows we think are worth your time.",
        preheader="3 upcoming shows we picked for you",
        cta_url="https://greenroom.test/events",
        shows=[
            {
                "headliner": "Phoebe Bridgers",
                "venue": "9:30 Club",
                "date_label": "Saturday, May 2 · 8:00 PM",
                "image_url": "https://example.com/img.jpg",
                "url": "https://greenroom.test/events/phoebe-bridgers",
            }
        ],
    )


def _wire_user_and_prefs(
    monkeypatch: pytest.MonkeyPatch,
    user: MagicMock,
    prefs: MagicMock,
) -> None:
    monkeypatch.setattr(
        notifications.users_repo, "get_user_by_id", lambda *_a, **_k: user
    )
    monkeypatch.setattr(
        notifications.prefs_repo, "get_or_create_for_user", lambda *_a, **_k: prefs
    )
    # Default the cap and idempotency guards to "go" — individual tests
    # override the relevant one when they care.
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "count_recent_for_user",
        lambda *_a, **_k: 0,
    )


def test_skip_when_assembler_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No content → no email and no log row."""
    user = _user()
    prefs = _prefs()
    _wire_user_and_prefs(monkeypatch, user, prefs)

    monkeypatch.setattr(notifications, "assemble_weekly_digest", lambda *_a, **_k: None)
    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )
    logged: list[Any] = []
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "create_log",
        lambda *a, **k: logged.append((a, k)),
    )

    result = notifications.send_weekly_digest_to_user(MagicMock(), user.id)
    assert result is False
    assert sent == []
    assert logged == []


def test_skip_when_already_sent_in_last_six_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weekly log within the trailing 6 days short-circuits the send."""
    user = _user()
    prefs = _prefs()
    _wire_user_and_prefs(monkeypatch, user, prefs)

    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    recent_log = MagicMock(sent_at=now - timedelta(days=2))
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: recent_log,
    )

    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )

    result = notifications.send_weekly_digest_to_user(MagicMock(), user.id, now=now)
    assert result is False
    assert sent == []


def test_send_proceeds_when_last_log_is_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A log older than 6 days does not block a fresh send."""
    user = _user()
    prefs = _prefs()
    _wire_user_and_prefs(monkeypatch, user, prefs)

    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    stale_log = MagicMock(sent_at=now - timedelta(days=8))
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: stale_log,
    )
    monkeypatch.setattr(
        notifications, "assemble_weekly_digest", lambda *_a, **_k: _digest()
    )
    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )
    logged: list[Any] = []
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "create_log",
        lambda *a, **k: logged.append((a, k)),
    )

    assert notifications.send_weekly_digest_to_user(MagicMock(), user.id, now=now)
    assert len(sent) == 1
    assert len(logged) == 1


def test_skip_when_at_weekly_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user at their max_emails_per_week cap is not sent to."""
    user = _user()
    prefs = _prefs(max_emails_per_week=1)
    _wire_user_and_prefs(monkeypatch, user, prefs)

    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        notifications.digest_log_repo, "count_recent_for_user", lambda *_a, **_k: 1
    )

    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )
    assert notifications.send_weekly_digest_to_user(MagicMock(), user.id) is False
    assert sent == []


def test_happy_path_composes_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compose_email gets the assembled context; a log row is written."""
    user = _user(email="riley@example.test")
    prefs = _prefs()
    _wire_user_and_prefs(monkeypatch, user, prefs)

    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        notifications.digest_log_repo, "count_recent_for_user", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        notifications, "assemble_weekly_digest", lambda *_a, **_k: _digest()
    )
    captured: dict[str, Any] = {}

    def fake_compose(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(notifications.email_service, "compose_email", fake_compose)

    log_calls: list[dict[str, Any]] = []

    def fake_create_log(_session: Any, **kwargs: Any) -> Any:
        log_calls.append(kwargs)
        return MagicMock(id=uuid.uuid4())

    monkeypatch.setattr(notifications.digest_log_repo, "create_log", fake_create_log)

    assert notifications.send_weekly_digest_to_user(MagicMock(), user.id) is True

    assert captured["to"] == "riley@example.test"
    assert captured["user_id"] == user.id
    assert captured["scope"] == "weekly_digest"
    assert captured["template"] == "show_announcement"
    assert "shows" in captured["context"]
    assert captured["context"]["heading"] == "Your week ahead"

    assert len(log_calls) == 1
    assert log_calls[0]["user_id"] == user.id
    assert log_calls[0]["digest_type"] == "weekly"
    assert log_calls[0]["event_count"] == 1


def test_no_log_row_when_compose_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Resend failure must not burn the user's weekly slot."""
    user = _user()
    prefs = _prefs()
    _wire_user_and_prefs(monkeypatch, user, prefs)

    monkeypatch.setattr(
        notifications.digest_log_repo,
        "get_most_recent_for_type",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        notifications.digest_log_repo, "count_recent_for_user", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        notifications, "assemble_weekly_digest", lambda *_a, **_k: _digest()
    )

    def boom(**_kw: Any) -> None:
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="Resend failed.",
            status_code=502,
        )

    monkeypatch.setattr(notifications.email_service, "compose_email", boom)

    logged: list[Any] = []
    monkeypatch.setattr(
        notifications.digest_log_repo,
        "create_log",
        lambda *a, **k: logged.append(k),
    )

    with pytest.raises(AppError):
        notifications.send_weekly_digest_to_user(MagicMock(), user.id)
    assert logged == []


def test_skip_when_user_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user that no longer exists is a quiet no-op, not a crash."""
    monkeypatch.setattr(
        notifications.users_repo, "get_user_by_id", lambda *_a, **_k: None
    )
    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )
    assert notifications.send_weekly_digest_to_user(MagicMock(), uuid.uuid4()) is False
    assert sent == []


def test_skip_when_user_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A globally-paused user is skipped even if other guards say go."""
    user = _user()
    prefs = _prefs(paused_at=datetime(2026, 1, 1, tzinfo=UTC))
    _wire_user_and_prefs(monkeypatch, user, prefs)

    sent: list[Any] = []
    monkeypatch.setattr(
        notifications.email_service, "compose_email", lambda **kw: sent.append(kw)
    )
    assert notifications.send_weekly_digest_to_user(MagicMock(), user.id) is False
    assert sent == []
