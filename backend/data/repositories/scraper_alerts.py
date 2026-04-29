"""Repository functions for scraper-alert dedup tracking.

The notifier consults this module before delivering a Slack or email
alert. Each alert carries a stable ``alert_key`` (e.g.
``"zero_results:black-cat"``) and a cooldown window. A delivery is
suppressed when the previous send for the same key falls inside the
window — keeping a single broken venue from drowning the on-call
channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from backend.data.models.scraper import ScraperAlert

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_alert(session: Session, alert_key: str) -> ScraperAlert | None:
    """Fetch the alert record for a given key.

    Args:
        session: Active SQLAlchemy session.
        alert_key: Stable, human-readable alert identifier.

    Returns:
        The matching ScraperAlert row, or None if the alert has never
        been recorded.
    """
    stmt = select(ScraperAlert).where(ScraperAlert.alert_key == alert_key)
    return session.execute(stmt).scalar_one_or_none()


def should_suppress(
    session: Session,
    alert_key: str,
    cooldown_hours: float,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if an alert with this key was sent within the cooldown.

    Args:
        session: Active SQLAlchemy session.
        alert_key: Stable, human-readable alert identifier.
        cooldown_hours: Suppression window in hours. Non-positive values
            disable suppression — every send is allowed through.
        now: Override the current timestamp; primarily for tests.

    Returns:
        True when a previous send falls inside the cooldown window and
        a fresh delivery should be skipped, False otherwise.
    """
    if cooldown_hours <= 0:
        return False
    record = get_alert(session, alert_key)
    if record is None:
        return False
    current = now if now is not None else datetime.now(UTC)
    last_sent = record.last_sent_at
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=UTC)
    return (current - last_sent) < timedelta(hours=cooldown_hours)


def record_alert(
    session: Session,
    *,
    alert_key: str,
    severity: str,
    title: str,
    message: str,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ScraperAlert:
    """Upsert the dedup row for a delivered alert.

    Bumps ``last_sent_at`` to ``now`` (or the supplied override) and
    increments ``sent_count``. The row's ``severity``, ``title``,
    ``message``, and ``details`` columns are overwritten with the most
    recent send so the daily digest can quote the latest payload.

    Args:
        session: Active SQLAlchemy session.
        alert_key: Stable, human-readable alert identifier.
        severity: Severity recorded for this send.
        title: Title delivered to Slack/email.
        message: Body delivered to Slack/email.
        details: Optional structured detail payload.
        now: Override the current timestamp; primarily for tests.

    Returns:
        The freshly inserted or updated ScraperAlert row.
    """
    current = now if now is not None else datetime.now(UTC)
    record = get_alert(session, alert_key)
    if record is None:
        record = ScraperAlert(
            alert_key=alert_key,
            last_sent_at=current,
            severity=severity,
            title=title,
            message=message,
            details=details,
            sent_count=1,
        )
        session.add(record)
    else:
        record.last_sent_at = current
        record.severity = severity
        record.title = title
        record.message = message
        record.details = details
        record.sent_count = (record.sent_count or 0) + 1
    session.flush()
    return record


def list_recent_alerts(
    session: Session,
    *,
    since: datetime,
) -> list[ScraperAlert]:
    """Return alert records whose most recent send is at or after ``since``.

    Powers the daily fleet-health digest — "what fired in the last 24
    hours, and how often did it have to fight through the cooldown?"

    Args:
        session: Active SQLAlchemy session.
        since: Lower bound on ``last_sent_at`` (inclusive).

    Returns:
        ScraperAlert rows ordered by most recent first.
    """
    stmt = (
        select(ScraperAlert)
        .where(ScraperAlert.last_sent_at >= since)
        .order_by(ScraperAlert.last_sent_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def count_active_alerts(
    session: Session,
    *,
    since: datetime,
) -> int:
    """Count alerts whose most recent send is at or after ``since``.

    Args:
        session: Active SQLAlchemy session.
        since: Lower bound on ``last_sent_at`` (inclusive).

    Returns:
        Number of distinct alert keys that fired in the window.
    """
    stmt = select(func.count()).where(ScraperAlert.last_sent_at >= since)
    return session.execute(stmt).scalar_one()
