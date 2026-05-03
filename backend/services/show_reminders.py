"""Show reminder dispatch.

The hourly :func:`dispatch_24h_reminders` task scans every saved-show
row whose start time falls inside the trailing "roughly 24 hours from
now" window in the user's timezone, and dispatches a push reminder
per match. Quiet hours, dedupe, rate limit, and channel preferences
are all enforced by :mod:`backend.services.notification_dispatcher` —
this module just produces triggers.

Why a one-hour-wide window: the dispatcher fires hourly. A user with
quiet hours 21..8 whose saved show starts at 19:00 tomorrow would
otherwise either get pinged at 19:00 today (the exact 24-hour
anniversary, but still inside quiet hours) or miss the reminder
entirely. A 23-25h window catches the show in either the 8 AM or
9 AM dispatcher run, and the dispatcher's dedupe prevents both runs
from sending two notifications.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.data.models.events import Event
from backend.data.models.users import SavedEvent, User
from backend.services import notification_dispatcher
from backend.services.notification_dispatcher import (
    NotificationTrigger,
    NotificationType,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)


# Width of the 24-hour-out window. Slightly wider than one hour so a
# clock-skewed dispatcher run still catches the boundary cases.
_WINDOW_LOWER_HOURS: int = 23
_WINDOW_UPPER_HOURS: int = 25


def dispatch_24h_reminders(session: Session) -> dict[str, int]:
    """Dispatch a push reminder for every saved show ~24 hours away.

    Args:
        session: Active SQLAlchemy session. The caller commits.

    Returns:
        Summary dict suitable for a single structured log line:
        ``{"candidates": N, "dispatched": N, "skipped": N}``.
    """
    now = datetime.now(UTC)
    lower = now + timedelta(hours=_WINDOW_LOWER_HOURS)
    upper = now + timedelta(hours=_WINDOW_UPPER_HOURS)

    stmt = (
        select(SavedEvent, Event, User)
        .join(Event, Event.id == SavedEvent.event_id)
        .join(User, User.id == SavedEvent.user_id)
        .where(Event.starts_at >= lower)
        .where(Event.starts_at <= upper)
        .options(joinedload(Event.venue))
    )
    rows = session.execute(stmt).all()

    summary = {"candidates": len(rows), "dispatched": 0, "skipped": 0}

    for _saved, event, user in rows:
        url = _build_event_url(event)
        venue_name = event.venue.name if event.venue else ""
        artist_name = (event.artists[0] if event.artists else event.title) or "Show"
        doors_label = _format_doors_label(event)

        trigger = NotificationTrigger(
            user_id=user.id,
            notification_type=NotificationType.SHOW_REMINDER_24H,
            dedupe_key=str(event.id),
            payload={
                "event_id": str(event.id),
                "performer_name": artist_name,
                "venue_name": venue_name,
                "doors_label": doors_label,
                "url": url,
            },
            trigger_time=now,
        )
        result = notification_dispatcher.dispatch(session, trigger)
        if result.push == "sent" or result.queued_until is not None:
            summary["dispatched"] += 1
        else:
            summary["skipped"] += 1

    return summary


def _build_event_url(event: Event) -> str:
    """Return the public URL for an event detail page.

    Args:
        event: The event to link to.

    Returns:
        Absolute URL of the event's public detail page.
    """
    base = get_settings().frontend_base_url.rstrip("/")
    return f"{base}/events/{event.slug}"


def _format_doors_label(event: Event) -> str | None:
    """Return a friendly "Doors 7pm" string in the venue's timezone.

    Args:
        event: The event whose ``doors_at`` should be formatted.

    Returns:
        A short label like ``"7:00 PM"``, or ``None`` when the event
        has no ``doors_at`` value (most scrapers don't supply one).
    """
    doors = getattr(event, "doors_at", None)
    if doors is None:
        return None
    if doors.tzinfo is None:
        doors = doors.replace(tzinfo=UTC)
    tz_name = (
        event.venue.city.timezone
        if event.venue and event.venue.city
        else "America/New_York"
    )
    label: str = doors.astimezone(ZoneInfo(tz_name)).strftime("%-I:%M %p")
    return label
