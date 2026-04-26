"""Notification-preference route handlers.

Endpoints under ``/me/notification-preferences`` let an authenticated
user read, patch, pause, and resume their email preferences. Each
handler is thin: it reads JSON, hands off to the service layer, and
returns the serialized row in the standard envelope.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.services import notification_preferences as prefs_service


@api_v1.route("/me/notification-preferences", methods=["GET"])
@require_auth
def get_notification_preferences() -> tuple[dict[str, Any], int]:
    """Return the authenticated user's notification preferences.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    user = get_current_user()
    prefs = prefs_service.get_preferences_for_user(session, user.id)
    return {"data": prefs_service.serialize_preferences(prefs)}, 200


@api_v1.route("/me/notification-preferences", methods=["PATCH"])
@require_auth
def patch_notification_preferences() -> tuple[dict[str, Any], int]:
    """Apply a partial update to the authenticated user's preferences.

    Patchable fields are the per-type toggles, the digest schedule, the
    weekly cap, the quiet-hours window, and the timezone. The handler
    validates only that the body is a JSON object — type-coercion lives
    in the service layer.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        ValidationError: If the body is not a JSON object or any field
            value fails coercion in the service layer.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    session = get_db()
    user = get_current_user()
    prefs = prefs_service.update_preferences_for_user(session, user.id, payload)
    return {"data": prefs_service.serialize_preferences(prefs)}, 200


@api_v1.route("/me/notification-preferences/pause-all", methods=["POST"])
@require_auth
def pause_all_emails() -> tuple[dict[str, Any], int]:
    """Globally pause every email type for the authenticated user.

    Per-type flags are preserved so the original choices come back
    when the pause is lifted. While paused, the per-type flags are
    interpreted as ``False`` by every send path.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    user = get_current_user()
    prefs = prefs_service.pause_all_emails(session, user.id)
    return {"data": prefs_service.serialize_preferences(prefs)}, 200


@api_v1.route("/me/notification-preferences/resume-all", methods=["POST"])
@require_auth
def resume_all_emails() -> tuple[dict[str, Any], int]:
    """Lift a global pause and restore the per-type flags as they were.

    No-op when the user is not currently paused.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    user = get_current_user()
    prefs = prefs_service.resume_all_emails(session, user.id)
    return {"data": prefs_service.serialize_preferences(prefs)}, 200
