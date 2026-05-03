"""Celery tasks that drive the email-digest and push pipelines.

Kept separate from :mod:`backend.services.notifications` so the pure
service module (used inside request/response handlers and unit tests)
does not import Celery just to be importable. Workers load this
module via ``celery_app.include``.

Tasks that live here:

* :func:`dispatch_weekly_digests_task` — hourly fan-out. The beat
  schedule fires it on the top of every hour; it walks every active
  weekly subscriber, filters by per-user timezone, and enqueues a
  per-user task for each due row.
* :func:`send_weekly_digest_task` — per-user worker. Owns its own
  session and re-checks the cap and idempotency guards inside the
  task on purpose, so a duplicate beat fire cannot fan out two
  emails to the same recipient.
* :func:`dispatch_tour_announcements_for_event_task` — fans out a
  tour-announcement push for every user with signal for any of a
  freshly-added event's performers. Enqueued by the scraper after
  ``create_event`` succeeds; a Celery hop keeps the scraper layer
  free of any service-module imports.
* :func:`dispatch_show_reminders_task` — hourly day-before reminder
  scan. Walks saved-show rows whose start time falls inside the
  trailing 23-25 hour window in the user's timezone and dispatches
  a push reminder per match.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.services import notifications

logger = get_logger(__name__)


@shared_task(
    name="backend.services.notification_tasks.dispatch_weekly_digests",
)  # type: ignore[untyped-decorator]
def dispatch_weekly_digests_task() -> dict[str, int]:
    """Fan out the weekly digest to every user due in the current hour.

    Owns its own DB session: commits on success (so the digest_log
    rows from any inline send are persisted), rolls back on error.
    The fan-out itself uses an inline ``send_fn`` rather than enqueuing
    per-user subtasks so the scheduler doesn't need a fleet of workers
    to deliver a few hundred emails — a single worker can drain an
    hour's bucket inside the task time limit.

    Returns:
        Summary dict from :func:`notifications.dispatch_weekly_digests`,
        passed through verbatim. The Celery result backend serializes
        this so the result is grep-able from `celery inspect`.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            summary = notifications.dispatch_weekly_digests(session)
            session.commit()
            logger.info("weekly_digest_dispatch_complete", extra=summary)
            return summary
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.notification_tasks.send_weekly_digest",
    bind=True,
    max_retries=3,
    default_retry_delay=60 * 5,
)  # type: ignore[untyped-decorator]
def send_weekly_digest_task(self: Any, user_id: str) -> dict[str, Any]:
    """Send the weekly digest to a single user.

    Exists as a Celery task (separate from the inline path the hourly
    dispatcher takes) so a user can be re-queued ad hoc — for example
    from an admin "resend" button or a backfill script. Uses Celery's
    automatic retry on transient errors with exponential backoff.

    Args:
        user_id: UUID string of the recipient.

    Returns:
        Dict with ``user_id`` and ``sent`` (bool), where ``sent`` is
        ``True`` when an email was actually delivered and ``False``
        when a guard short-circuited the send.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            sent = notifications.send_weekly_digest_to_user(session, uuid.UUID(user_id))
            session.commit()
            return {"user_id": user_id, "sent": sent}
        except Exception as exc:
            session.rollback()
            raise self.retry(exc=exc) from exc


@shared_task(
    name="backend.services.notification_tasks.dispatch_tour_announcements_for_event",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)  # type: ignore[untyped-decorator]
def dispatch_tour_announcements_for_event_task(
    self: Any, event_id: str
) -> dict[str, int]:
    """Fan out tour-announcement pushes for one freshly-added event.

    Triggered from the scraper ingestion pipeline immediately after a
    new ``Event`` row is created. Routed through Celery rather than
    called inline so the scraper layer stays free of any service-
    module imports (per the architectural layer rules in CLAUDE.md).

    Args:
        event_id: UUID string of the just-created event.

    Returns:
        Summary from
        :func:`backend.services.tour_announcements.dispatch_for_event`.
    """
    from backend.services import tour_announcements

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            summary = tour_announcements.dispatch_for_event(
                session, uuid.UUID(event_id)
            )
            session.commit()
            logger.info(
                "tour_announcements_dispatched",
                extra={"event_id": event_id, **summary},
            )
            return summary
        except Exception as exc:
            session.rollback()
            raise self.retry(exc=exc) from exc


@shared_task(
    name="backend.services.notification_tasks.dispatch_show_reminders",
)  # type: ignore[untyped-decorator]
def dispatch_show_reminders_task() -> dict[str, int]:
    """Fan out day-before show reminders for every saved upcoming show.

    Hourly fan-out: walks saved-show rows whose start time is roughly
    24 hours from now (in the user's local timezone), and dispatches
    a push reminder per match. Quiet hours and dedupe are enforced by
    the unified dispatcher.

    Returns:
        Summary dict the Celery wrapper logs as a single line.
    """
    from backend.services import show_reminders

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            summary = show_reminders.dispatch_24h_reminders(session)
            session.commit()
            logger.info("show_reminders_dispatched", extra=summary)
            return summary
        except Exception:
            session.rollback()
            raise
