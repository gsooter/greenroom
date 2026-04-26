"""Notification-preference business logic.

The API layer calls these functions for every read/patch/pause/resume
of the per-user email preferences. Each patch goes through
:func:`_validated_updates` so an attacker can't force an out-of-range
hour or an unknown enum value into the row.

Boolean fields default to the on/off splits documented in the email
sprint plan: actionable alerts default on, discovery emails default
off, the digest defaults off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.core.exceptions import ValidationError
from backend.data.models.notifications import (
    DigestDayOfWeek,
    NotificationPreferences,
)
from backend.data.repositories import notification_preferences as prefs_repo

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


# Per-type flags. Toggling these matters for emails sent in response
# to events; they are subject to the global pause/resume.
_BOOLEAN_FIELDS: frozenset[str] = frozenset(
    {
        "artist_announcements",
        "venue_announcements",
        "selling_fast_alerts",
        "show_reminders",
        "staff_picks",
        "artist_spotlights",
        "similar_artist_suggestions",
        "weekly_digest",
    }
)

# Allowed values for the integer fields that have hard whitelists
# (rather than ranges). Defined here so the service and CHECK
# constraint agree.
_RESERVED_REMINDER_DAYS: frozenset[int] = frozenset({1, 2, 7})
_RESERVED_MAX_PER_WEEK: frozenset[int] = frozenset({1, 3, 7})

_PATCHABLE_FIELDS: frozenset[str] = _BOOLEAN_FIELDS | frozenset(
    {
        "show_reminder_days_before",
        "digest_day_of_week",
        "digest_hour",
        "max_emails_per_week",
        "quiet_hours_start",
        "quiet_hours_end",
        "timezone",
    }
)


# ---------------------------------------------------------------------------
# Read / write entry points
# ---------------------------------------------------------------------------


def get_preferences_for_user(
    session: Session, user_id: uuid.UUID
) -> NotificationPreferences:
    """Return the user's preference row, creating defaults if missing.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The :class:`NotificationPreferences` row.
    """
    return prefs_repo.get_or_create_for_user(session, user_id)


def update_preferences_for_user(
    session: Session,
    user_id: uuid.UUID,
    patch: dict[str, Any],
) -> NotificationPreferences:
    """Apply a partial update to a user's notification preferences.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user whose preferences should change.
        patch: Mapping of field names to new values from the caller.

    Returns:
        The updated :class:`NotificationPreferences` row.

    Raises:
        ValidationError: If the payload is not a JSON object or any
            value fails type or range validation.
    """
    if not isinstance(patch, dict):
        raise ValidationError("Request body must be a JSON object.")
    updates = _validated_updates(patch)
    prefs = prefs_repo.get_or_create_for_user(session, user_id)
    if not updates:
        return prefs
    return prefs_repo.update_preferences(session, prefs, **updates)


def pause_all_emails(session: Session, user_id: uuid.UUID) -> NotificationPreferences:
    """Pause every email for a user without erasing their per-type flags.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The mutated :class:`NotificationPreferences` row.
    """
    prefs = prefs_repo.get_or_create_for_user(session, user_id)
    return prefs_repo.pause_all(session, prefs)


def resume_all_emails(session: Session, user_id: uuid.UUID) -> NotificationPreferences:
    """Restore the per-type flags captured by :func:`pause_all_emails`.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The mutated :class:`NotificationPreferences` row.
    """
    prefs = prefs_repo.get_or_create_for_user(session, user_id)
    return prefs_repo.resume_all(session, prefs)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize_preferences(prefs: NotificationPreferences) -> dict[str, Any]:
    """Serialize a preference row for the authenticated API response.

    Returns the per-type flags in their stored state (so the UI can
    render the toggles), plus a top-level ``paused`` boolean and
    ``paused_at`` timestamp so the UI knows whether the per-type
    toggles are currently effective.

    Args:
        prefs: The :class:`NotificationPreferences` row.

    Returns:
        A JSON-safe dict.
    """
    return {
        "artist_announcements": prefs.artist_announcements,
        "venue_announcements": prefs.venue_announcements,
        "selling_fast_alerts": prefs.selling_fast_alerts,
        "show_reminders": prefs.show_reminders,
        "show_reminder_days_before": prefs.show_reminder_days_before,
        "staff_picks": prefs.staff_picks,
        "artist_spotlights": prefs.artist_spotlights,
        "similar_artist_suggestions": prefs.similar_artist_suggestions,
        "weekly_digest": prefs.weekly_digest,
        "digest_day_of_week": prefs.digest_day_of_week,
        "digest_hour": prefs.digest_hour,
        "max_emails_per_week": prefs.max_emails_per_week,
        "quiet_hours_start": prefs.quiet_hours_start,
        "quiet_hours_end": prefs.quiet_hours_end,
        "timezone": prefs.timezone,
        "paused": prefs.paused_at is not None,
        "paused_at": prefs.paused_at.isoformat() if prefs.paused_at else None,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validated_updates(patch: dict[str, Any]) -> dict[str, Any]:
    """Filter, type-check, and coerce a preference patch payload.

    Args:
        patch: Raw mapping from the request body.

    Returns:
        A dict of vetted field/value pairs ready for the repository.
        Unknown fields are silently dropped — consistent with the
        pattern used by ``users_service._validated_updates``.

    Raises:
        ValidationError: If a value has the wrong type or is out of
            range for its field.
    """
    updates: dict[str, Any] = {}
    for field, value in patch.items():
        if field not in _PATCHABLE_FIELDS:
            continue

        if field in _BOOLEAN_FIELDS:
            updates[field] = _coerce_bool(field, value)
        elif field == "show_reminder_days_before":
            updates[field] = _coerce_choice_int(field, value, _RESERVED_REMINDER_DAYS)
        elif field == "digest_day_of_week":
            updates[field] = _coerce_day_of_week(value)
        elif field in {"digest_hour", "quiet_hours_start", "quiet_hours_end"}:
            updates[field] = _coerce_hour(field, value)
        elif field == "max_emails_per_week":
            updates[field] = _coerce_max_per_week(value)
        elif field == "timezone":
            updates[field] = _coerce_timezone(value)
    return updates


def _coerce_bool(field: str, value: Any) -> bool:
    """Coerce a boolean field, rejecting truthy/falsy non-bool values.

    Args:
        field: Name of the field being validated (used in the error
            message).
        value: Raw value from the request body.

    Returns:
        The boolean as-is when type-correct.

    Raises:
        ValidationError: If ``value`` is not a Python ``bool``.
    """
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be a boolean.")
    return value


def _coerce_choice_int(
    field: str,
    value: Any,
    allowed: frozenset[int],
) -> int:
    """Coerce an integer field whose value must come from a fixed set.

    Args:
        field: Name of the field being validated.
        value: Raw value from the request body.
        allowed: The set of accepted integer values.

    Returns:
        The integer when it is in the allowed set.

    Raises:
        ValidationError: If the value is not an int or is out of set.
    """
    # ``bool`` is a subclass of ``int`` in Python — exclude it explicitly.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer.")
    if value not in allowed:
        allowed_str = ", ".join(str(v) for v in sorted(allowed))
        raise ValidationError(f"{field} must be one of: {allowed_str}.")
    return value


def _coerce_hour(field: str, value: Any) -> int:
    """Coerce an hour-of-day field to an int in 0..23.

    Args:
        field: Name of the field being validated.
        value: Raw value from the request body.

    Returns:
        The hour as an integer.

    Raises:
        ValidationError: If the value is not an int or is out of range.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer.")
    if value < 0 or value > 23:
        raise ValidationError(f"{field} must be between 0 and 23.")
    return value


def _coerce_max_per_week(value: Any) -> int | None:
    """Coerce ``max_emails_per_week`` to an int in {1, 3, 7} or None.

    Args:
        value: Raw value from the request body.

    Returns:
        Either an integer in the allowed set or None for "unlimited".

    Raises:
        ValidationError: If the value is not None or an allowed integer.
    """
    if value is None:
        return None
    return _coerce_choice_int("max_emails_per_week", value, _RESERVED_MAX_PER_WEEK)


def _coerce_day_of_week(value: Any) -> str:
    """Coerce a ``digest_day_of_week`` value to a lowercase weekday name.

    Args:
        value: Raw value from the request body.

    Returns:
        The lowercase weekday name when it matches a valid enum value.

    Raises:
        ValidationError: If the value is not a valid weekday string.
    """
    if not isinstance(value, str):
        raise ValidationError("digest_day_of_week must be a string.")
    normalized = value.strip().lower()
    try:
        return DigestDayOfWeek(normalized).value
    except ValueError as exc:
        allowed = ", ".join(d.value for d in DigestDayOfWeek)
        raise ValidationError(f"digest_day_of_week must be one of: {allowed}.") from exc


def _coerce_timezone(value: Any) -> str:
    """Coerce a ``timezone`` value to a known IANA zone string.

    Args:
        value: Raw value from the request body.

    Returns:
        The IANA timezone string when ``zoneinfo`` resolves it.

    Raises:
        ValidationError: If the value is not a string or is not a
            known IANA zone on this system.
    """
    if not isinstance(value, str):
        raise ValidationError("timezone must be a string.")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"timezone '{value}' is not a known IANA zone.") from exc
    return value
