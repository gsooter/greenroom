"""Recommendation route handlers.

Authenticated endpoints for the ``/me/recommendations`` feed that
backs the For-You page. Listing lazily regenerates on the first read
after login; an explicit POST is available for a manual refresh.
"""

from __future__ import annotations

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.services import recommendations as recs_service


@api_v1.route("/me/recommendations", methods=["GET"])
@require_auth
def list_my_recommendations() -> tuple[dict, int]:
    """Return the authenticated user's paginated recommendation list.

    Query parameters:
        page: 1-indexed page number (default 1).
        per_page: rows per page (default 20, max 100).

    When the caller has Spotify artists cached but no persisted
    recommendations yet, the service layer will regenerate before
    returning so the response is never empty on first use.

    Returns:
        Tuple of JSON body (``{data, meta}``) and HTTP 200.

    Raises:
        ValidationError: If ``per_page`` exceeds 100.
    """
    session = get_db()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    user = get_current_user()
    recs, total = recs_service.list_recommendations_for_user(
        session, user, page=page, per_page=per_page
    )

    return {
        "data": [recs_service.serialize_recommendation(r) for r in recs],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/me/recommendations/refresh", methods=["POST"])
@require_auth
def refresh_my_recommendations() -> tuple[dict, int]:
    """Force a regeneration of the caller's recommendation list.

    Intended for a "Refresh" button on the For-You page — the client
    POSTs this and then re-reads :func:`list_my_recommendations`.

    Returns:
        Tuple of JSON body (``{data: {generated}}``) and HTTP 200.
    """
    session = get_db()
    user = get_current_user()
    count = recs_service.refresh_recommendations_for_user(session, user)
    return {"data": {"generated": count}}, 200
