"""Saved event route handlers.

Authenticated endpoints for adding, removing, and listing the caller's
saved events. Lives in its own module (not ``users.py``) because the
save/unsave paths are namespaced under ``/events`` rather than ``/me``.
"""

from __future__ import annotations

import uuid

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.data.repositories import users as users_repo
from backend.services import saved_events as saved_events_service


@api_v1.route("/events/<event_id>/save", methods=["POST"])
@require_auth
def save_event(event_id: str) -> tuple[dict, int]:
    """Save an event for the authenticated user.

    Idempotent — returns 200 with the existing saved-event record if
    the caller has already saved this event, or 201 if the row was
    just created.

    Args:
        event_id: UUID string of the event to save.

    Returns:
        Tuple of JSON response body and HTTP 201 (created) or 200
        (already saved) status code.

    Raises:
        ValidationError: If ``event_id`` is not a valid UUID.
        NotFoundError: If the event does not exist.
    """
    session = get_db()
    parsed_id = _parse_event_id(event_id)
    user = get_current_user()

    # Check whether a row already exists before calling the service so
    # we can return the right status code.
    was_new = users_repo.get_saved_event(session, user.id, parsed_id) is None

    saved = saved_events_service.save_event(session, user, parsed_id)
    status = 201 if was_new else 200
    return {"data": saved_events_service.serialize_saved_event(saved)}, status


@api_v1.route("/events/<event_id>/save", methods=["DELETE"])
@require_auth
def unsave_event(event_id: str) -> tuple[dict, int]:
    """Remove an event from the authenticated user's saved list.

    Idempotent — returns 204 whether a record was deleted or the event
    was not saved to begin with, so stale clients don't error on a
    duplicate unsave click.

    Args:
        event_id: UUID string of the event to unsave.

    Returns:
        Tuple of empty body and HTTP 204 status code.

    Raises:
        ValidationError: If ``event_id`` is not a valid UUID.
    """
    session = get_db()
    parsed_id = _parse_event_id(event_id)
    saved_events_service.unsave_event(session, get_current_user(), parsed_id)
    return {}, 204


@api_v1.route("/me/saved-events", methods=["GET"])
@require_auth
def list_saved_events() -> tuple[dict, int]:
    """List the authenticated user's saved events.

    Query parameters:
        page: int — page number (default 1).
        per_page: int — results per page (default 20, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        ValidationError: If ``per_page`` exceeds 100.
    """
    session = get_db()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    user = get_current_user()
    saved, total = saved_events_service.list_saved_events(
        session, user, page=page, per_page=per_page
    )

    return {
        "data": [
            saved_events_service.serialize_saved_event(s) for s in saved
        ],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


def _parse_event_id(value: str) -> uuid.UUID:
    """Parse a path parameter as a UUID.

    Args:
        value: String pulled out of the URL.

    Returns:
        Parsed UUID.

    Raises:
        ValidationError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(
            f"event_id is not a valid UUID: '{value}'"
        ) from exc
