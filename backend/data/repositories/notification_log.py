"""Repository for ``notification_log`` rows.

The dispatcher uses this module to claim a (user, type, key, channel)
slot before sending. The unique constraint guarantees that a second
attempt — whether from a duplicate scraper run, a retried Celery task,
or a replayed trigger — short-circuits cleanly without depending on
"first read, then write" race-condition gymnastics.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.data.models.notification_log import NotificationLog

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def claim(
    session: Session,
    *,
    user_id: uuid.UUID,
    notification_type: str,
    dedupe_key: str,
    channel: str,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Attempt to insert a log row; return ``False`` if a duplicate exists.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the recipient.
        notification_type: Type identifier (e.g. ``tour_announcement``).
        dedupe_key: Trigger-anchored key (event UUID, ISO week, etc.).
        channel: ``"push"`` or ``"email"``.
        payload: Optional JSONB payload for diagnostics.
        now: Override the wall clock; defaults to ``datetime.now(UTC)``.

    Returns:
        ``True`` if the row was inserted (the dispatcher should
        proceed with the actual send). ``False`` if the unique
        constraint already had a matching row (a previous send
        already happened — short-circuit).
    """
    sent_at = now or datetime.now(UTC)
    row = NotificationLog(
        user_id=user_id,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
        channel=channel,
        sent_at=sent_at,
        payload=payload,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def count_recent_pushes(
    session: Session,
    user_id: uuid.UUID,
    *,
    window_hours: int = 24,
    now: datetime | None = None,
) -> int:
    """Count push log rows for a user inside the trailing window.

    Used by the dispatcher's per-day rate-limit guard.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to count for.
        window_hours: Trailing window length. Defaults to 24 hours.
        now: Override the wall clock; defaults to ``datetime.now(UTC)``.

    Returns:
        Number of push-channel log rows whose ``sent_at`` falls inside
        the window.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=window_hours)
    stmt = select(func.count(NotificationLog.id)).where(
        NotificationLog.user_id == user_id,
        NotificationLog.channel == "push",
        NotificationLog.sent_at >= since,
    )
    return int(session.execute(stmt).scalar_one() or 0)


def list_recent_for_user(
    session: Session,
    user_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[NotificationLog]:
    """List the most-recent notification log rows for a user.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        limit: Maximum rows to return.

    Returns:
        Newest-first list of log rows. Used by the admin "recent
        notifications" pane and by the integration tests that assert
        the dispatcher fired the expected channel.
    """
    stmt = (
        select(NotificationLog)
        .where(NotificationLog.user_id == user_id)
        .order_by(NotificationLog.sent_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())
