"""Event route handlers.

Thin handlers that validate input and delegate to the events service.
No business logic lives here.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from flask import Response, request

from backend.api.v1 import api_v1
from backend.core.auth import try_get_current_user
from backend.core.database import get_db
from backend.data.repositories import events as events_repo
from backend.data.repositories import notification_preferences as prefs_repo
from backend.services import events as events_service
from backend.services import tickets as tickets_service


@api_v1.route("/events", methods=["GET"])
def list_events() -> tuple[dict[str, Any], int]:
    """List events with optional filters and pagination.

    Query parameters:
        city_id: UUID — filter to a specific city.
        region: string — filter to cities in this region (e.g., "DMV").
        venue_id: UUID (repeatable) — filter to specific venues.
        date_from: YYYY-MM-DD — start of date range. Defaults to today
            when omitted so the public listing only surfaces upcoming
            shows; pass an explicit value to query historical events.
        date_to: YYYY-MM-DD — end of date range.
        genre: string (repeatable) — filter by genre overlap.
        artist_id: UUID (repeatable) — filter to events whose Spotify
            artist IDs overlap any of these enriched artists.
        artist_search: string — case-insensitive substring on the
            ``artists`` array.
        price_max: float — upper bound on ``min_price``. Drops unpriced
            events.
        free_only: ``"true"`` to restrict to free shows.
        available_only: ``"true"`` to drop cancelled, sold-out, and past
            events.
        event_type: string — filter by event type.
        status: string — filter by event status.
        sort: ``"date"`` (default) or ``"for_you"``. ``for_you`` reads
            the optional bearer token and orders by the caller's
            persisted recommendation scores; anonymous callers fall
            back to date order.
        day_of_week: int (repeatable) — filter to weekdays 0..6 (Sun..Sat).
        time_of_day: string (repeatable) — bucket name from
            ``early`` / ``evening`` / ``late``.
        has_image: ``"true"`` to drop events without ``image_url``.
        has_price: ``"true"`` to drop events without ``min_price``.
        followed_venues_only: ``"true"`` to restrict to followed venues
            (requires bearer token).
        followed_artists_only: ``"true"`` to restrict to events whose
            Spotify artists overlap the caller's enriched follows
            (requires bearer token).
        page: int — page number (default 1).
        per_page: int — results per page (default 20, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()

    city_id = _parse_uuid(request.args.get("city_id"))
    region = request.args.get("region")
    venue_ids = _parse_uuid_list(request.args.getlist("venue_id"))
    artist_ids = _parse_uuid_list(request.args.getlist("artist_id"))
    artist_search = request.args.get("artist_search")
    price_max = request.args.get("price_max", type=float)
    free_only = _parse_bool(request.args.get("free_only"))
    available_only = _parse_bool(request.args.get("available_only"))
    raw_date_from = request.args.get("date_from")
    today_flag = _parse_bool(request.args.get("today"))
    requested_timezone = request.args.get("timezone")
    if raw_date_from is not None:
        date_from: date | None = _parse_date(raw_date_from)
    elif today_flag:
        # The today=true filter computes its own UTC bounds in the
        # service layer; leave date_from None so the service knows to
        # use the timezone-aware path.
        date_from = None
    else:
        date_from = date.today()
    date_to = _parse_date(request.args.get("date_to"))
    genres = request.args.getlist("genre") or None
    event_type = request.args.get("event_type")
    status = request.args.get("status")
    sort = request.args.get("sort")
    day_of_week = _parse_int_list(request.args.getlist("day_of_week"))
    time_of_day = request.args.getlist("time_of_day") or None
    has_image = _parse_bool(request.args.get("has_image"))
    has_price = _parse_bool(request.args.get("has_price"))
    followed_venues_only = _parse_bool(request.args.get("followed_venues_only"))
    followed_artists_only = _parse_bool(request.args.get("followed_artists_only"))
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    needs_user = (
        sort == "for_you" or followed_venues_only or followed_artists_only or today_flag
    )
    user = try_get_current_user() if needs_user else None

    # Timezone resolution: explicit ``timezone`` param wins; otherwise
    # the authenticated caller's stored preference is used; otherwise
    # the service falls back to America/New_York.
    timezone_name = requested_timezone
    if today_flag and timezone_name is None and user is not None:
        prefs = prefs_repo.get_for_user(session, user.id)
        if prefs is not None and prefs.timezone:
            timezone_name = prefs.timezone

    events, total = events_service.list_events(
        session,
        city_id=city_id,
        region=region,
        venue_ids=venue_ids,
        date_from=date_from,
        date_to=date_to,
        today=today_flag,
        timezone_name=timezone_name,
        genres=genres,
        artist_ids=artist_ids,
        artist_search=artist_search,
        price_max=price_max,
        free_only=free_only,
        available_only=available_only,
        event_type=event_type,
        status=status,
        sort=sort,
        user_id=user.id if user is not None else None,
        day_of_week=day_of_week,
        time_of_day=time_of_day,
        has_image=has_image,
        has_price=has_price,
        followed_venues_only=followed_venues_only,
        followed_artists_only=followed_artists_only,
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

    payload = events_service.serialize_event(event)
    payload["pricing"] = tickets_service.serialize_pricing_state(session, event)
    return {"data": payload}, 200


@api_v1.route("/events/<event_id>/pricing", methods=["GET"])
def get_event_pricing(event_id: str) -> tuple[dict[str, Any], int]:
    """Return the multi-source pricing state for one event.

    Lighter than ``GET /events/<id>`` — used by the SSR detail page when
    only the pricing block needs to refresh after a manual sweep.

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

    return {"data": tickets_service.serialize_pricing_state(session, event)}, 200


@api_v1.route("/events/<event_id>/refresh-pricing", methods=["POST"])
def refresh_event_pricing(event_id: str) -> tuple[dict[str, Any], int]:
    """Trigger a manual pricing sweep for one event.

    The handler enforces the cooldown via the service layer; a request
    inside the window short-circuits and returns the persisted state
    without calling any upstream APIs. The response always carries the
    full serialized pricing state so the caller can re-render without a
    second round-trip.

    Args:
        event_id: UUID string or slug of the event.

    Returns:
        Tuple of JSON response body and HTTP 200 status code. The body
        carries ``refresh`` (the :class:`RefreshResult` summary) and
        ``pricing`` (the merged sources payload).
    """
    session = get_db()

    parsed_id = _parse_uuid(event_id)
    if parsed_id is not None:
        event = events_service.get_event(session, parsed_id)
    else:
        event = events_service.get_event_by_slug(session, event_id)

    result = tickets_service.refresh_event_pricing(session, event)
    pricing = tickets_service.serialize_pricing_state(session, event)

    return {
        "data": {
            "refresh": {
                "event_id": str(result.event_id),
                "refreshed_at": result.refreshed_at.isoformat(),
                "cooldown_active": result.cooldown_active,
                "quotes_persisted": result.quotes_persisted,
                "links_upserted": result.links_upserted,
                "provider_errors": list(result.provider_errors),
            },
            "pricing": pricing,
        }
    }, 200


@api_v1.route("/pricing/freshness", methods=["GET"])
def get_pricing_freshness() -> tuple[dict[str, Any], int]:
    """Return the most recent pricing-sweep timestamp across all upcoming events.

    Powers the listing-page "Pricing updated X ago" banner. Cheap
    indexed MAX query — no per-event traversal, no provider calls.

    Returns:
        Tuple of JSON body and HTTP 200 status code. ``refreshed_at``
        is an ISO 8601 string, or ``null`` if nothing has been swept.
    """
    session = get_db()
    refreshed_at = events_repo.get_latest_pricing_refresh(session)
    return {
        "data": {
            "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        }
    }, 200


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
        generated_at=datetime.now(UTC),
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


def _parse_int_list(values: list[str]) -> list[int] | None:
    """Parse a list of strings as ints, dropping unparseable entries.

    Args:
        values: Raw repeated query-string values
            (e.g., ``?day_of_week=0&day_of_week=5``).

    Returns:
        List of parsed ints, or ``None`` when the input is empty so the
        downstream filter is skipped.
    """
    if not values:
        return None
    parsed: list[int] = []
    for v in values:
        try:
            parsed.append(int(v))
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


def _parse_bool(value: str | None) -> bool:
    """Coerce a query-string flag to ``bool`` with truthy short-forms.

    Treats ``"true"``, ``"1"``, ``"yes"``, ``"on"`` (case-insensitive)
    as True and everything else (including ``None``) as False, so the
    common idioms ``?free_only=1`` and ``?free_only=true`` both work
    without forcing the frontend onto a single spelling.

    Args:
        value: Raw query-string value or None.

    Returns:
        Parsed boolean.
    """
    if value is None:
        return False
    return value.strip().lower() in {"true", "1", "yes", "on"}
