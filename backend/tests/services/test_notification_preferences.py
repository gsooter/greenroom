"""Unit tests for :mod:`backend.services.notification_preferences`.

The service layer is the validation surface for the new
``/me/notification-preferences`` endpoints. Tests exercise every
coerce branch (booleans, day-of-week, integer ranges, timezone) plus
serialization and the pause / resume round-trip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import ValidationError
from backend.data.models.notifications import NotificationPreferences
from backend.services import notification_preferences as prefs_service

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


def _stub_prefs(**overrides: Any) -> NotificationPreferences:
    """Build a NotificationPreferences instance for serialization tests.

    Args:
        **overrides: Field overrides applied on top of the defaults.

    Returns:
        A NotificationPreferences populated with sensible defaults.
    """
    defaults: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "artist_announcements": True,
        "venue_announcements": True,
        "selling_fast_alerts": True,
        "show_reminders": True,
        "show_reminder_days_before": 1,
        "staff_picks": False,
        "artist_spotlights": False,
        "similar_artist_suggestions": False,
        "weekly_digest": False,
        "digest_day_of_week": "monday",
        "digest_hour": 8,
        "max_emails_per_week": 3,
        "quiet_hours_start": 21,
        "quiet_hours_end": 8,
        "timezone": "America/New_York",
        "paused_at": None,
        "paused_snapshot": None,
    }
    defaults.update(overrides)
    return NotificationPreferences(**defaults)


# ---------------------------------------------------------------------------
# _validated_updates — boolean fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "artist_announcements",
        "venue_announcements",
        "selling_fast_alerts",
        "show_reminders",
        "staff_picks",
        "artist_spotlights",
        "similar_artist_suggestions",
        "weekly_digest",
    ],
)
def test_boolean_fields_accept_true_and_false(field: str) -> None:
    """Every per-type toggle accepts true/false bools."""
    assert prefs_service._validated_updates({field: True})[field] is True
    assert prefs_service._validated_updates({field: False})[field] is False


def test_boolean_field_rejects_non_bool() -> None:
    """Strings, ints, and dicts are not accepted as booleans."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"weekly_digest": "yes"})
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"weekly_digest": 1})


def test_validated_updates_drops_unknown_fields() -> None:
    """Fields outside the allowlist are silently dropped."""
    assert prefs_service._validated_updates({"is_admin": True}) == {}


# ---------------------------------------------------------------------------
# show_reminder_days_before
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 2, 7])
def test_reminder_days_accepts_allowed_values(value: int) -> None:
    """Only 1, 2, and 7 are valid reminder-day windows."""
    assert (
        prefs_service._validated_updates({"show_reminder_days_before": value})[
            "show_reminder_days_before"
        ]
        == value
    )


@pytest.mark.parametrize("value", [0, 3, 14, -1])
def test_reminder_days_rejects_other_values(value: int) -> None:
    """Out-of-set integers are rejected with a ValidationError."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"show_reminder_days_before": value})


def test_reminder_days_rejects_non_int() -> None:
    """A string value is rejected even if it parses as a number."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"show_reminder_days_before": "1"})


# ---------------------------------------------------------------------------
# digest_day_of_week
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ],
)
def test_day_of_week_accepts_each_day(value: str) -> None:
    """Every English weekday name is accepted."""
    result = prefs_service._validated_updates({"digest_day_of_week": value})
    assert result["digest_day_of_week"] == value


def test_day_of_week_normalizes_case() -> None:
    """Mixed-case input is folded to lowercase before validation."""
    result = prefs_service._validated_updates({"digest_day_of_week": "Monday"})
    assert result["digest_day_of_week"] == "monday"


def test_day_of_week_rejects_non_day() -> None:
    """A non-weekday string is rejected."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"digest_day_of_week": "funday"})


# ---------------------------------------------------------------------------
# digest_hour, quiet_hours_start, quiet_hours_end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["digest_hour", "quiet_hours_start", "quiet_hours_end"]
)
@pytest.mark.parametrize("value", [0, 6, 12, 23])
def test_hour_fields_accept_range(field: str, value: int) -> None:
    """0..23 is the valid range for every hour-of-day field."""
    assert prefs_service._validated_updates({field: value})[field] == value


@pytest.mark.parametrize(
    "field", ["digest_hour", "quiet_hours_start", "quiet_hours_end"]
)
@pytest.mark.parametrize("value", [-1, 24, 99])
def test_hour_fields_reject_out_of_range(field: str, value: int) -> None:
    """Values outside 0..23 are rejected."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({field: value})


# ---------------------------------------------------------------------------
# max_emails_per_week
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 3, 7])
def test_max_per_week_accepts_finite_caps(value: int) -> None:
    """Only 1, 3, or 7 are valid finite caps."""
    assert (
        prefs_service._validated_updates({"max_emails_per_week": value})[
            "max_emails_per_week"
        ]
        == value
    )


def test_max_per_week_accepts_null_for_unlimited() -> None:
    """Null clears the cap (interpreted as unlimited)."""
    assert (
        prefs_service._validated_updates({"max_emails_per_week": None})[
            "max_emails_per_week"
        ]
        is None
    )


@pytest.mark.parametrize("value", [0, 2, 5, 10])
def test_max_per_week_rejects_other_values(value: int) -> None:
    """Integers outside the allowed set are rejected."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"max_emails_per_week": value})


# ---------------------------------------------------------------------------
# timezone
# ---------------------------------------------------------------------------


def test_timezone_accepts_iana_string() -> None:
    """A real IANA zone passes the zoneinfo round-trip."""
    result = prefs_service._validated_updates({"timezone": "America/Los_Angeles"})
    assert result["timezone"] == "America/Los_Angeles"


def test_timezone_rejects_garbage() -> None:
    """An unknown IANA string is rejected."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"timezone": "Nowhere/Madeup"})


def test_timezone_rejects_non_string() -> None:
    """Type mismatch is rejected before zoneinfo is consulted."""
    with pytest.raises(ValidationError):
        prefs_service._validated_updates({"timezone": 5})


# ---------------------------------------------------------------------------
# serialize_preferences
# ---------------------------------------------------------------------------


def test_serialize_renders_paused_false_when_active() -> None:
    """An active row reports paused=False with paused_at None."""
    payload = prefs_service.serialize_preferences(_stub_prefs())
    assert payload["paused"] is False
    assert payload["paused_at"] is None
    assert payload["weekly_digest"] is False
    assert payload["timezone"] == "America/New_York"


def test_serialize_renders_paused_true_with_iso_timestamp() -> None:
    """A paused row reports paused=True with the timestamp serialized."""
    paused_at = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    payload = prefs_service.serialize_preferences(
        _stub_prefs(paused_at=paused_at, paused_snapshot={"weekly_digest": True})
    )
    assert payload["paused"] is True
    assert payload["paused_at"] == paused_at.isoformat()


# ---------------------------------------------------------------------------
# update_preferences_for_user — service entry point
# ---------------------------------------------------------------------------


def test_update_preferences_for_user_routes_through_repo(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service applies validated patch via the repository.

    Mocks the repo so we assert the full pipeline: get_or_create →
    validation → update_preferences with the coerced kwargs.
    """
    user_id = uuid.uuid4()
    prefs = _stub_prefs(user_id=user_id)

    captured: dict[str, Any] = {}

    def fake_get_or_create(_s: Any, uid: uuid.UUID) -> NotificationPreferences:
        assert uid == user_id
        return prefs

    def fake_update(
        _s: Any, p: NotificationPreferences, **updates: Any
    ) -> NotificationPreferences:
        captured["updates"] = updates
        for key, value in updates.items():
            setattr(p, key, value)
        return p

    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.get_or_create_for_user",
        fake_get_or_create,
    )
    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.update_preferences",
        fake_update,
    )

    result = prefs_service.update_preferences_for_user(
        session, user_id, {"weekly_digest": True, "digest_hour": 18}
    )

    assert captured["updates"] == {"weekly_digest": True, "digest_hour": 18}
    assert result.weekly_digest is True
    assert result.digest_hour == 18


def test_update_preferences_for_user_rejects_non_dict(session: MagicMock) -> None:
    """A non-dict payload is a ValidationError before any DB call."""
    with pytest.raises(ValidationError):
        prefs_service.update_preferences_for_user(
            session,
            uuid.uuid4(),
            "not-a-dict",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# pause_all / resume_all entry points
# ---------------------------------------------------------------------------


def test_pause_all_emails_delegates_to_repo(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service pause helper resolves prefs and calls the repo."""
    prefs = _stub_prefs()
    calls: list[NotificationPreferences] = []

    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.get_or_create_for_user",
        lambda _s, _uid: prefs,
    )
    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.pause_all",
        lambda _s, p: calls.append(p) or p,
    )

    result = prefs_service.pause_all_emails(session, uuid.uuid4())

    assert calls == [prefs]
    assert result is prefs


def test_resume_all_emails_delegates_to_repo(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service resume helper resolves prefs and calls the repo."""
    prefs = _stub_prefs()
    calls: list[NotificationPreferences] = []

    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.get_or_create_for_user",
        lambda _s, _uid: prefs,
    )
    monkeypatch.setattr(
        "backend.services.notification_preferences.prefs_repo.resume_all",
        lambda _s, p: calls.append(p) or p,
    )

    result = prefs_service.resume_all_emails(session, uuid.uuid4())

    assert calls == [prefs]
    assert result is prefs
