"""Saved events business logic — save, unsave, and list.

The API layer calls these functions and never accesses the saved
events repository directly. Serialization reuses the canonical event
serializer from :mod:`backend.services.events` so saved-events
payloads match the shape returned by the browse endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError
from backend.data.repositories import events as events_repo
from backend.data.repositories import users as users_repo
from backend.services import events as events_service

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.users import SavedEvent, User


def save_event(session: Session, user: User, event_id: uuid.UUID) -> SavedEvent:
    """Save an event for the authenticated user.

    Idempotent — calling this twice with the same ``(user, event)``
    pair returns the existing record rather than creating a duplicate
    (the DB has no unique constraint today and we don't want one row
    per click).

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user saving the event.
        event_id: UUID of the event to save.

    Returns:
        The :class:`SavedEvent` record, either newly created or existing.

    Raises:
        NotFoundError: If the event does not exist.
    """
    if events_repo.get_event_by_id(session, event_id) is None:
        raise NotFoundError(
            code=EVENT_NOT_FOUND,
            message=f"No event found with id {event_id}",
        )

    existing = users_repo.get_saved_event(session, user.id, event_id)
    if existing is not None:
        return existing

    return users_repo.create_saved_event(session, user_id=user.id, event_id=event_id)


def unsave_event(session: Session, user: User, event_id: uuid.UUID) -> bool:
    """Remove an event from the authenticated user's saved list.

    Idempotent — returns ``False`` if the event was not saved, so the
    caller can still respond 204 without a 404 round-trip on a stale
    UI click.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        event_id: UUID of the event to unsave.

    Returns:
        True if a row was deleted, False if there was nothing to delete.
    """
    saved = users_repo.get_saved_event(session, user.id, event_id)
    if saved is None:
        return False
    users_repo.delete_saved_event(session, saved)
    return True


def list_saved_events(
    session: Session,
    user: User,
    *,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[SavedEvent], int]:
    """List the authenticated user's saved events with pagination.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 20.

    Returns:
        Tuple of (saved events list, total count).
    """
    return users_repo.list_saved_events(session, user.id, page=page, per_page=per_page)


def serialize_saved_event(saved: SavedEvent) -> dict[str, Any]:
    """Serialize a saved event record for the API response.

    Returns the underlying event in its compact list-view shape plus a
    ``saved_at`` timestamp so the UI can sort by save recency without
    a second request.

    Args:
        saved: The :class:`SavedEvent` to serialize.

    Returns:
        Dictionary representation of the saved event.
    """
    return {
        "saved_at": saved.created_at.isoformat(),
        "event": events_service.serialize_event_summary(saved.event),
    }
