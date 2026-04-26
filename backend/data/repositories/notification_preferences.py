"""Repository for the ``notification_preferences`` table.

Every email path consults the row returned by
:func:`get_or_create_for_user` before sending. The migration backfills
a row for every existing user, but the lazy-create path here exists so
a brand-new user (created between migrations) still gets a default
record on first read instead of a 404.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.data.models.notifications import NotificationPreferences

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

# Per-type flag columns mirrored into ``paused_snapshot`` when the user
# globally pauses email. Listed once so pause and resume agree on the
# set of fields that are subject to the pause toggle.
_TOGGLE_FIELDS: tuple[str, ...] = (
    "artist_announcements",
    "venue_announcements",
    "selling_fast_alerts",
    "show_reminders",
    "staff_picks",
    "artist_spotlights",
    "similar_artist_suggestions",
    "weekly_digest",
)


def get_for_user(
    session: Session, user_id: uuid.UUID
) -> NotificationPreferences | None:
    """Fetch the preference row for a user without creating one.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The ``NotificationPreferences`` row if present, otherwise None.
    """
    stmt = select(NotificationPreferences).where(
        NotificationPreferences.user_id == user_id
    )
    return session.execute(stmt).scalar_one_or_none()


def get_or_create_for_user(
    session: Session, user_id: uuid.UUID
) -> NotificationPreferences:
    """Fetch the preference row for a user, inserting defaults if needed.

    Used by the read and patch paths. The migration backfills a row
    for every existing user, but a user created between migrations
    (or seeded outside Alembic) still needs the lazy-create path.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The ``NotificationPreferences`` row, freshly created with
        column defaults if one did not exist.
    """
    existing = get_for_user(session, user_id)
    if existing is not None:
        return existing
    prefs = NotificationPreferences(user_id=user_id)
    session.add(prefs)
    session.flush()
    return prefs


def update_preferences(
    session: Session,
    preferences: NotificationPreferences,
    **updates: Any,
) -> NotificationPreferences:
    """Apply a vetted patch to a preference row.

    The service layer is responsible for whitelisting and coercing
    values; this function simply assigns and flushes. Unknown kwargs
    are ignored (consistent with :func:`backend.data.repositories.users.update_user`)
    so a stray attribute name doesn't raise from a deep call site.

    Args:
        session: Active SQLAlchemy session.
        preferences: The row to mutate.
        **updates: Column names and new values.

    Returns:
        The mutated preference row.
    """
    for key, value in updates.items():
        if hasattr(preferences, key):
            setattr(preferences, key, value)
    session.flush()
    return preferences


def pause_all(
    session: Session,
    preferences: NotificationPreferences,
) -> NotificationPreferences:
    """Snapshot per-type flags and stamp ``paused_at`` for global pause.

    Idempotent: calling pause on an already-paused row leaves the
    original snapshot intact. The service layer treats any row with
    ``paused_at IS NOT NULL`` as "all flags effectively False" without
    rewriting the per-column flags themselves — this preserves the
    user's choices for restore time.

    Args:
        session: Active SQLAlchemy session.
        preferences: The preference row to pause.

    Returns:
        The mutated preference row.
    """
    if preferences.paused_at is not None:
        return preferences

    snapshot: dict[str, Any] = {
        field: getattr(preferences, field) for field in _TOGGLE_FIELDS
    }
    preferences.paused_snapshot = snapshot
    preferences.paused_at = datetime.now(UTC)
    session.flush()
    return preferences


def resume_all(
    session: Session,
    preferences: NotificationPreferences,
) -> NotificationPreferences:
    """Restore the snapshot captured by :func:`pause_all` and clear pause.

    No-op when the row is not paused. The snapshot is consumed (set
    back to ``None``) so a future pause captures fresh state.

    Args:
        session: Active SQLAlchemy session.
        preferences: The preference row to resume.

    Returns:
        The mutated preference row.
    """
    if preferences.paused_at is None and preferences.paused_snapshot is None:
        return preferences

    snapshot = preferences.paused_snapshot or {}
    for field in _TOGGLE_FIELDS:
        if field in snapshot:
            setattr(preferences, field, bool(snapshot[field]))
    preferences.paused_at = None
    preferences.paused_snapshot = None
    session.flush()
    return preferences
