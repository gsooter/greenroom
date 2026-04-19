"""Celery tasks that keep per-user Spotify data fresh.

Kept separate from ``backend.services.spotify`` so the pure HTTP client
(used inside request/response handlers) does not import Celery just to
be importable. Workers load this module via ``celery_app.include``.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import users as users_repo
from backend.services import spotify as spotify_service

logger = get_logger(__name__)


@shared_task(name="backend.services.spotify_tasks.sync_user_spotify_data")  # type: ignore[untyped-decorator]
def sync_user_spotify_data(user_id: str) -> dict[str, Any]:
    """Refresh a user's cached Spotify top-artist snapshot.

    Owns its own DB session: commits on success, rolls back on error.
    Swallows ``AppError`` from Spotify (logged at WARNING) because a
    stale snapshot is better than a retry storm when a user has revoked
    access; the next login will re-trigger the sync.

    Args:
        user_id: UUID string of the user to sync.

    Returns:
        Dict with ``user_id`` and ``synced`` (count of artists stored),
        or ``synced=0`` if the user no longer has a linked Spotify
        account.
    """
    uid = uuid.UUID(user_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            user = users_repo.get_user_by_id(session, uid)
            if user is None or not user.is_active:
                logger.info("sync_user_spotify_data: skipping %s (missing)", uid)
                return {"user_id": user_id, "synced": 0}
            count = spotify_service.sync_top_artists(session, user)
            session.commit()
            return {"user_id": user_id, "synced": count}
        except Exception:
            session.rollback()
            raise
