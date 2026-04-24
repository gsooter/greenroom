"""Celery tasks that keep per-user Apple Music data fresh.

Kept separate from :mod:`backend.services.apple_music` so the pure HTTP
client (used inside request/response handlers) does not import Celery
just to be importable. Workers load this module via
``celery_app.include``.

Apple Music differs from Spotify in that there is no refresh-token
path — the Music User Token is long-lived, and a stale MUT is a signal
the user revoked access. When :func:`sync_user_apple_music_data`
encounters that failure it logs and returns zero rather than retrying,
because a retry storm against a revoked token is useless.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider
from backend.data.repositories import users as users_repo
from backend.services import apple_music as apple_music_service

logger = get_logger(__name__)


@shared_task(name="backend.services.apple_music_tasks.sync_user_apple_music_data")  # type: ignore[untyped-decorator]
def sync_user_apple_music_data(user_id: str) -> dict[str, Any]:
    """Refresh a user's cached Apple Music artist snapshot.

    Owns its own DB session: commits on success, rolls back on error.
    Swallows ``AppError`` from Apple (logged at WARNING) because a
    stale snapshot is better than a retry storm when a user has
    revoked access; the next MusicKit authorize flow in the browser
    will re-trigger the sync end-to-end.

    Args:
        user_id: UUID string of the user to sync.

    Returns:
        Dict with ``user_id`` and ``synced`` (count of artists stored),
        or ``synced=0`` if the user no longer has a linked Apple Music
        account.
    """
    uid = uuid.UUID(user_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            user = users_repo.get_user_by_id(session, uid)
            if user is None or not user.is_active:
                logger.info("sync_user_apple_music_data: skipping %s (missing)", uid)
                return {"user_id": user_id, "synced": 0}
            connection = next(
                (
                    c
                    for c in user.music_connections
                    if c.provider == OAuthProvider.APPLE_MUSIC
                ),
                None,
            )
            if connection is None or not connection.access_token:
                return {"user_id": user_id, "synced": 0}
            count = apple_music_service.sync_top_artists(
                session,
                user,
                music_user_token=connection.access_token,
            )
            session.commit()
            return {"user_id": user_id, "synced": count}
        except Exception:
            session.rollback()
            raise
