"""Event route handlers.

Thin handlers that validate input and delegate to the events service.
No business logic lives here.
"""

import uuid
from datetime import date, datetime
from typing import Any

from flask import Response, request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.services import events as events_service


@api_v1.route("/events", methods=["GET"])
def list_events() -> tuple[dict[str, Any], int]:
    """List events with optional filters and pagination.

    Query parameters:
        city_id: UUID — filter to a specific city.
        region: string — filter to cities in this region (e.g., "DMV").
        venue_id: UUID (repeatable) — filter to specific venues.
        date_from: YYYY-MM-DD — start of date range.
        date_to: YYYY-MM-DD — end of date range.
        genre: string (repeatable) — filter by genre overlap.
        event_type: string — filter by event type.
        status: string — filter by event status.
        page: int — page number (default 1).
        per_page: int — results per page (default 20, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()

    city_id = _parse_uuid(request.args.get("city_id"))
    region = request.args.get("region")
    venue_ids = _parse_uuid_list(request.args.getlist("venue_id"))
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))
    genres = request.args.getlist("genre") or None
    event_type = request.args.get("event_type")
    status = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    events, total = events_service.list_events(
        session,
        city_id=city_id,
        region=region,
        venue_ids=venue_ids,
        date_from=date_from,
        date_to=date_to,
        genres=genres,
        event_type=event_type,
        status=status,
        page=page,
        per_page=per_page,
    )

    return {
        "data": [events_service.serialize_event_summary(e) for e in events],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/events/<event_id>", methods=["GET"])
def get_event(event_id: str) -> tuple[dict[str, Any], int]:
    """Fetch a single event by ID or slug.

    Args:
        event_id: UUID string or slug of the event.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()

    parsed_id = _parse_uuid(event_id)
    if parsed_id is not None:
        event = events_service.get_event(session, parsed_id)
    else:
        event = events_service.get_event_by_slug(session, event_id)

    return {"data": events_service.serialize_event(event)}, 200


@api_v1.route("/feed/events", methods=["GET"])
def event_feed() -> Response:
    """Plain text event feed optimized for AI crawler consumption.

    Returns a human- and AI-readable text response of upcoming events.
    Defaults to the DMV region. No authentication required.

    Query parameters:
        region: string — region to feed (default "DMV").
        city_id: UUID — override region and scope to a single city.

    Returns:
        Plain text response with upcoming events.
    """
    session = get_db()

    city_id = _parse_uuid(request.args.get("city_id"))
    region = request.args.get("region") if city_id is None else None
    if city_id is None and region is None:
        region = "DMV"

    events, _ = events_service.list_events(
        session,
        city_id=city_id,
        region=region,
        date_from=date.today(),
        page=1,
        per_page=100,
    )

    feed_text = events_service.format_event_feed(
        events,
        generated_at=datetime.utcnow(),
    )

    return Response(feed_text, mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Input parsing helpers
# ---------------------------------------------------------------------------


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    """Parse a string as a UUID, returning None if invalid.

    Args:
        value: String to parse, or None.

    Returns:
        Parsed UUID or None.
    """
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _parse_uuid_list(values: list[str]) -> list[uuid.UUID] | None:
    """Parse a list of strings as UUIDs, skipping invalid values.

    Args:
        values: List of strings to parse.

    Returns:
        List of parsed UUIDs, or None if the input was empty.
    """
    if not values:
        return None
    parsed = []
    for v in values:
        try:
            parsed.append(uuid.UUID(v))
        except ValueError:
            continue
    return parsed or None


def _parse_date(value: str | None) -> date | None:
    """Parse a YYYY-MM-DD string as a date, returning None if invalid.

    Args:
        value: Date string to parse, or None.

    Returns:
        Parsed date or None.
    """
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
