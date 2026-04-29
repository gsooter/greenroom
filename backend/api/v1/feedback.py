"""Feedback route handlers.

Public ``POST /api/v1/feedback`` accepts a beta-feedback submission
from the in-app widget. Auth is optional — when a valid Knuckles token
is attached the row is associated with the user and the reply-to email
is taken from the account, otherwise the row is anonymous.

Admin endpoints under ``/api/v1/admin/feedback`` power the dashboard
view and let ops mark submissions as resolved.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.api.v1.admin import require_admin
from backend.core.auth import try_get_current_user
from backend.core.database import get_db
from backend.core.exceptions import (
    FEEDBACK_NOT_FOUND,
    NotFoundError,
    ValidationError,
)
from backend.core.rate_limit import rate_limit
from backend.services import feedback as feedback_service


@api_v1.route("/feedback", methods=["POST"])
@rate_limit("feedback_submit_ip", limit=10, window_seconds=3600)
def submit_feedback() -> tuple[dict[str, Any], int]:
    """Accept a feedback submission from the in-app widget.

    Authentication is optional — when a valid bearer token is present
    the row is tagged with that user's id and account email; otherwise
    the form-supplied email (if any) is stored verbatim.

    Request body fields:
        message: Required, freeform text up to 4000 chars.
        kind: Required, one of ``bug``, ``feature``, ``general``.
        email: Optional, only used for anonymous submissions.
        page_url: Optional, the URL the user was on when submitting.

    Returns:
        Tuple of JSON response body and HTTP 201 status code.

    Raises:
        ValidationError: If ``message`` or ``kind`` is missing or
            invalid.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    message = payload.get("message")
    kind = payload.get("kind")
    email = payload.get("email")
    page_url = payload.get("page_url")
    user_agent = request.headers.get("User-Agent")

    if not isinstance(message, str):
        raise ValidationError("Field 'message' is required and must be a string.")
    if not isinstance(kind, str):
        raise ValidationError("Field 'kind' is required and must be a string.")
    if email is not None and not isinstance(email, str):
        raise ValidationError("Field 'email' must be a string.")
    if page_url is not None and not isinstance(page_url, str):
        raise ValidationError("Field 'page_url' must be a string.")

    session = get_db()
    user = try_get_current_user()
    row = feedback_service.submit_feedback(
        session,
        message=message,
        kind=kind,
        user=user,
        email=email,
        page_url=page_url,
        user_agent=user_agent,
    )
    return {"data": feedback_service.serialize_feedback(row)}, 201


@api_v1.route("/admin/feedback", methods=["GET"])
@require_admin
def admin_list_feedback() -> tuple[dict[str, Any], int]:
    """List feedback submissions for the admin dashboard.

    Query parameters:
        kind: ``bug`` / ``feature`` / ``general`` — filter by category.
        is_resolved: ``true`` / ``false`` — filter by triage state.
        page: int — page number (default 1).
        per_page: int — page size (default 25, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    kind = request.args.get("kind")
    is_resolved = request.args.get("is_resolved")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    rows, total = feedback_service.list_feedback(
        session,
        kind=kind,
        is_resolved=is_resolved,
        page=page,
        per_page=per_page,
    )

    return {
        "data": [feedback_service.serialize_feedback(r) for r in rows],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/admin/feedback/<feedback_id>/resolve", methods=["POST"])
@require_admin
def admin_resolve_feedback(feedback_id: str) -> tuple[dict[str, Any], int]:
    """Mark a submission as resolved or unresolved.

    Body field:
        is_resolved: bool — defaults to ``true`` when omitted so the
            common "click resolve" flow does not need a body.

    Args:
        feedback_id: UUID of the submission to update.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If ``feedback_id`` is malformed or not found.
    """
    parsed = _parse_feedback_uuid(feedback_id)
    body = request.get_json(silent=True) or {}
    is_resolved = body.get("is_resolved", True)
    if not isinstance(is_resolved, bool):
        raise ValidationError("Field 'is_resolved' must be a boolean.")

    session = get_db()
    row = feedback_service.set_resolved(session, parsed, is_resolved=is_resolved)
    return {"data": feedback_service.serialize_feedback(row)}, 200


def _parse_feedback_uuid(value: str) -> uuid.UUID:
    """Parse a path-segment UUID or raise a 404.

    Args:
        value: Raw path segment from the URL.

    Returns:
        Parsed UUID.

    Raises:
        NotFoundError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(
            code=FEEDBACK_NOT_FOUND,
            message=f"No feedback found with id {value}",
        ) from exc
