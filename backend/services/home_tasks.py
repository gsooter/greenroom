"""Celery tasks for the home page.

Today this is a single fire-and-forget task that records the user's
home page visit timestamp. The home page route enqueues it after the
response is composed so the request thread never blocks on the write.
A dedicated task module (rather than calling ``shared_task`` from
``services/home.py``) keeps the route file's imports free of Celery so
the home page is unit-testable without a Celery configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.services import home as home_service

logger = get_logger(__name__)


@shared_task(name="backend.services.home_tasks.record_home_visit")  # type: ignore[untyped-decorator]
def record_home_visit(user_id: str) -> dict[str, Any]:
    """Update ``users.last_home_visit_at`` for the given user.

    Owns its own DB session: commits on success, rolls back on error.
    Misses (user no longer exists) are logged and silently swallowed —
    the home page already rendered, and a stale timestamp on a deleted
    account is not worth a retry storm.

    Args:
        user_id: UUID string of the user whose visit to record.

    Returns:
        Dict with ``user_id`` and ``recorded`` (True when the row was
        updated, False when the user could not be found).
    """
    uid = uuid.UUID(user_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            home_service.update_last_home_visit_at(session, uid)
            session.commit()
            return {"user_id": user_id, "recorded": True}
        except Exception:
            session.rollback()
            raise
