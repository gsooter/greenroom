"""Venue route handlers.

Thin handlers that validate input and delegate to the venues service.
No business logic lives here.
"""

import uuid

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.data.repositories import events as events_repo
from backend.services import events as events_service
from backend.services import venues as venues_service


@api_v1.route("/venues", methods=["GET"])
def list_venues() -> tuple[dict, int]:
    """List venues with pagination. At least one scope filter required.

    Query parameters:
        city_id: UUID — filter to a specific city.
        region: string — filter to cities in this region (e.g., "DMV").
        active_only: bool — only return active venues (default true).
        page: int — page number (default 1).
        per_page: int — results per page (default 50, max 100).

    Either `city_id` or `region` must be supplied.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()

    city_id_str = request.args.get("city_id")
    city_id: uuid.UUID | None = None
    if city_id_str is not None:
        try:
            city_id = uuid.UUID(city_id_str)
        except ValueError:
            return {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"Invalid city_id: '{city_id_str}'",
                }
            }, 422

    region = request.args.get("region")
    active_only = request.args.get("active_only", "true").lower() != "false"
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    venues, total = venues_service.list_venues(
        session,
        city_id=city_id,
        region=region,
        active_only=active_only,
        page=page,
        per_page=per_page,
    )

    return {
        "data": [venues_service.serialize_venue_summary(v) for v in venues],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/venues/<slug>", methods=["GET"])
def get_venue(slug: str) -> tuple[dict, int]:
    """Fetch a single venue by slug, including upcoming events.

    Args:
        slug: URL-safe slug identifier of the venue.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()

    venue = venues_service.get_venue_by_slug(session, slug)

    upcoming_events, event_count = events_repo.list_events_by_venue(
        session,
        venue.id,
        upcoming_only=True,
        page=1,
        per_page=20,
    )

    venue_data = venues_service.serialize_venue(venue)
    venue_data["upcoming_events"] = [
        events_service.serialize_event_summary(e) for e in upcoming_events
    ]
    venue_data["upcoming_event_count"] = event_count

    return {"data": venue_data}, 200
