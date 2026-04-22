"""Event business logic — search, filtering, and feed generation.

All event-related business logic lives here. API routes call these
functions and never access the repository layer directly.
"""

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError, ValidationError
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.repositories import events as events_repo

_ET_ZONE = ZoneInfo("America/New_York")

NearMeWindow = Literal["tonight", "week"]
_NEAR_ME_WINDOWS: frozenset[str] = frozenset({"tonight", "week"})
_EARTH_RADIUS_KM = 6371.0088


def get_event(session: Session, event_id: uuid.UUID) -> Event:
    """Fetch a single event by ID.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.

    Returns:
        The Event instance.

    Raises:
        NotFoundError: If the event does not exist.
    """
    event = events_repo.get_event_by_id(session, event_id)
    if event is None:
        raise NotFoundError(
            code=EVENT_NOT_FOUND,
            message=f"No event found with id {event_id}",
        )
    return event


def get_event_by_slug(session: Session, slug: str) -> Event:
    """Fetch a single event by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Event instance.

    Raises:
        NotFoundError: If the event does not exist.
    """
    event = events_repo.get_event_by_slug(session, slug)
    if event is None:
        raise NotFoundError(
            code=EVENT_NOT_FOUND,
            message=f"No event found with slug '{slug}'",
        )
    return event


def list_events(
    session: Session,
    *,
    city_id: uuid.UUID | None = None,
    region: str | None = None,
    venue_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    genres: list[str] | None = None,
    event_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """List events with optional filters and pagination.

    Args:
        session: Active SQLAlchemy session.
        city_id: Filter to events in a specific city.
        region: Filter to events in cities in this region (e.g., "DMV").
        venue_ids: Filter to specific venues.
        date_from: Start of date range.
        date_to: End of date range.
        genres: Filter by genre overlap.
        event_type: Filter by event type string.
        status: Filter by event status string.
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (events list, total count).

    Raises:
        ValidationError: If per_page exceeds 100 or enum values are invalid.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    parsed_type: EventType | None = None
    if event_type is not None:
        try:
            parsed_type = EventType(event_type.lower())
        except ValueError as err:
            raise ValidationError(
                f"Invalid event_type: '{event_type}'. "
                f"Valid values: {[e.value for e in EventType]}"
            ) from err

    parsed_status: EventStatus | None = None
    if status is not None:
        try:
            parsed_status = EventStatus(status.lower())
        except ValueError as err:
            raise ValidationError(
                f"Invalid status: '{status}'. "
                f"Valid values: {[s.value for s in EventStatus]}"
            ) from err

    return events_repo.list_events(
        session,
        city_id=city_id,
        region=region,
        venue_ids=venue_ids,
        date_from=date_from,
        date_to=date_to,
        genres=genres,
        event_type=parsed_type,
        status=parsed_status,
        page=page,
        per_page=per_page,
    )


def serialize_event(event: Event) -> dict[str, Any]:
    """Serialize an Event instance to a JSON-safe dictionary.

    Args:
        event: The Event instance to serialize.

    Returns:
        Dictionary representation of the event.
    """
    return {
        "id": str(event.id),
        "venue_id": str(event.venue_id),
        "title": event.title,
        "slug": event.slug,
        "description": event.description,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "doors_at": event.doors_at.isoformat() if event.doors_at else None,
        "artists": event.artists or [],
        "genres": event.genres or [],
        "spotify_artist_ids": event.spotify_artist_ids or [],
        "image_url": event.image_url,
        "ticket_url": event.ticket_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "source_url": event.source_url,
        "venue": _serialize_venue_with_city(event),
        "created_at": event.created_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
    }


def serialize_event_summary(event: Event) -> dict[str, Any]:
    """Serialize an Event to a compact summary for list views.

    Args:
        event: The Event instance to serialize.

    Returns:
        Compact dictionary representation of the event.
    """
    return {
        "id": str(event.id),
        "title": event.title,
        "slug": event.slug,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "artists": event.artists or [],
        "image_url": event.image_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "status": event.status.value,
        "venue": _serialize_venue_with_city(event),
    }


def _serialize_venue_with_city(event: Event) -> dict[str, Any] | None:
    """Serialize the event's venue with its city inline.

    Browse cards need venue name and the city/region the venue lives in.
    The relationship is configured with ``lazy="selectin"`` so this read
    does not trigger an extra round-trip per event.

    Args:
        event: The parent Event instance.

    Returns:
        Venue summary with nested city, or None if venue is unloaded.
    """
    if event.venue is None:
        return None
    city = event.venue.city
    return {
        "id": str(event.venue.id),
        "name": event.venue.name,
        "slug": event.venue.slug,
        "city": (
            {
                "id": str(city.id),
                "name": city.name,
                "slug": city.slug,
                "state": city.state,
                "region": city.region,
            }
            if city is not None
            else None
        ),
    }


def format_event_feed(events: list[Event], generated_at: datetime) -> str:
    """Format events as plain text for the AI-readable feed endpoint.

    Produces a human- and AI-readable text feed as specified in CLAUDE.md
    for the GET /api/v1/feed/events endpoint.

    Args:
        events: List of Event instances to include in the feed.
        generated_at: Timestamp for the feed header.

    Returns:
        Plain text string of the formatted event feed.
    """
    lines: list[str] = []
    lines.append(
        f"Washington DC Concerts — Updated {generated_at.strftime('%Y-%m-%d %H:%M ET')}"
    )
    lines.append("")

    today = generated_at.date()

    tonight_events = [e for e in events if e.starts_at and e.starts_at.date() == today]
    upcoming_events = [e for e in events if e.starts_at and e.starts_at.date() > today]

    if tonight_events:
        lines.append("TONIGHT")
        for event in tonight_events:
            lines.append(_format_feed_line(event))
        lines.append("")

    if upcoming_events:
        lines.append("UPCOMING")
        for event in upcoming_events:
            date_str = event.starts_at.strftime("%a %b %d")
            lines.append(_format_feed_line(event, date_prefix=date_str))
        lines.append("")

    return "\n".join(lines)


def list_tonight_map_events(
    session: Session,
    *,
    region: str = "DMV",
    now_utc: datetime | None = None,
    genres: list[str] | None = None,
) -> dict[str, Any]:
    """Return the envelope of today's pinnable events for the map surface.

    "Tonight" is defined as the current calendar day in Eastern time (the
    DMV region), so events scheduled late in the evening still count as
    tonight even when the UTC wallclock has rolled into the next day.
    Venues without coordinates are dropped because the map UI has no way
    to render them.

    Args:
        session: Active SQLAlchemy session.
        region: City region filter — defaults to ``"DMV"`` since this is
            the DC map. Exposed so the same function can be exercised
            from tests or other regions later.
        now_utc: Clock anchor in UTC. Injected in tests to pin the ET
            day window; defaults to :func:`datetime.now` in production.
        genres: Optional genre overlap filter for the filter bar.

    Returns:
        Standard envelope dict ``{"data": [...], "meta": {...}}`` where
        each row carries enough shape to render a map pin plus a
        preview card: id, slug, title, artists, genres, image_url,
        min_price, starts_at, and a venue block with latitude and
        longitude.
    """
    anchor = (now_utc or datetime.now(UTC)).astimezone(_ET_ZONE)
    today = anchor.date()

    events, _total = events_repo.list_events(
        session,
        region=region,
        date_from=today,
        date_to=today,
        genres=genres,
        status=EventStatus.CONFIRMED,
        page=1,
        per_page=100,
    )
    pins = [_serialize_tonight_event(event) for event in events if _has_coords(event)]
    return {
        "data": pins,
        "meta": {"count": len(pins), "date": today.isoformat()},
    }


def list_events_near(
    session: Session,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    window: NearMeWindow = "tonight",
    region: str = "DMV",
    limit: int = 50,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return upcoming events within ``radius_km`` of a lat/lng, nearest first.

    Powers the "Shows Near Me" surface. Unlike the Tonight map (which
    sweeps the whole DMV), this endpoint narrows to a radius around
    the user's current location and supports a small time window —
    ``"tonight"`` for today only, ``"week"`` for the next seven days.

    The distance filter is computed in Python via the haversine formula
    so the repo query doesn't need PostGIS. The DMV dataset is small
    enough (< 100 venues) that the post-fetch filter is cheap.

    Args:
        session: Active SQLAlchemy session.
        latitude: WGS-84 latitude of the user's current location.
        longitude: WGS-84 longitude of the user's current location.
        radius_km: Maximum great-circle distance to include, in km.
        window: ``"tonight"`` (today only, ET) or ``"week"`` (today
            through today + 6 days, ET).
        region: City region filter; defaults to ``"DMV"``.
        limit: Maximum rows returned after distance sort. Capped
            internally at 100 since the map surface renders one pin per.
        now_utc: Clock anchor in UTC, injected by tests to pin the ET
            day window. Defaults to :func:`datetime.now` in production.

    Returns:
        Standard envelope ``{"data": [...], "meta": {...}}``. Each row
        has the tonight-map pin shape plus a ``distance_km`` float.
        Meta echoes the caller's center, radius, window, and the
        resolved ``date_from`` / ``date_to`` bounds.

    Raises:
        ValidationError: If ``window`` is not one of the supported
            literals.
    """
    if window not in _NEAR_ME_WINDOWS:
        raise ValidationError(
            f"Invalid window: '{window}'. Valid values: {sorted(_NEAR_ME_WINDOWS)}"
        )
    capped_limit = max(1, min(limit, 100))

    anchor = (now_utc or datetime.now(UTC)).astimezone(_ET_ZONE)
    day_from = anchor.date()
    day_to = day_from if window == "tonight" else day_from + timedelta(days=6)

    events, _total = events_repo.list_events(
        session,
        region=region,
        date_from=day_from,
        date_to=day_to,
        status=EventStatus.CONFIRMED,
        page=1,
        per_page=200,
    )

    rows: list[dict[str, Any]] = []
    for event in events:
        if not _has_coords(event):
            continue
        venue = event.venue
        assert venue is not None  # narrowed by _has_coords
        distance = _haversine_km(
            latitude,
            longitude,
            venue.latitude,  # type: ignore[arg-type]
            venue.longitude,  # type: ignore[arg-type]
        )
        if distance > radius_km:
            continue
        payload = _serialize_tonight_event(event)
        payload["distance_km"] = round(distance, 3)
        rows.append(payload)

    rows.sort(key=lambda r: r["distance_km"])
    rows = rows[:capped_limit]

    return {
        "data": rows,
        "meta": {
            "count": len(rows),
            "center": {"latitude": latitude, "longitude": longitude},
            "radius_km": radius_km,
            "window": window,
            "date_from": day_from.isoformat(),
            "date_to": day_to.isoformat(),
        },
    }


def _haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance between two WGS-84 points in kilometres.

    Uses the standard haversine formula with the IUGG mean Earth radius
    (6371.0088 km) so distances are accurate to within ~0.3% for the
    sub-100-km ranges the near-me surface cares about.

    Args:
        lat1: Latitude of point A in decimal degrees.
        lon1: Longitude of point A in decimal degrees.
        lat2: Latitude of point B in decimal degrees.
        lon2: Longitude of point B in decimal degrees.

    Returns:
        Great-circle distance in kilometres.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c


def _has_coords(event: Event) -> bool:
    """Return True when the event's venue can be placed on a map.

    Args:
        event: Event instance with its venue relationship loaded.

    Returns:
        True if the venue relationship is present and both ``latitude``
        and ``longitude`` are non-null.
    """
    venue = event.venue
    return (
        venue is not None and venue.latitude is not None and venue.longitude is not None
    )


def _serialize_tonight_event(event: Event) -> dict[str, Any]:
    """Serialize an event into the compact shape the map surface consumes.

    Drops moderation-only fields (raw_data, source_url, external_id) and
    wraps the venue with just the fields the pin and preview card need
    (name, slug, and the two coordinates the map has to have).

    Args:
        event: An Event instance whose venue has coordinates.

    Returns:
        JSON-safe dict for the ``data`` array on ``/maps/tonight``.
    """
    venue = event.venue
    return {
        "id": str(event.id),
        "slug": event.slug,
        "title": event.title,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "artists": event.artists or [],
        "genres": event.genres or [],
        "image_url": event.image_url,
        "ticket_url": event.ticket_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "venue": {
            "id": str(venue.id),
            "name": venue.name,
            "slug": venue.slug,
            "latitude": venue.latitude,
            "longitude": venue.longitude,
        },
    }


def _format_feed_line(
    event: Event,
    date_prefix: str | None = None,
) -> str:
    """Format a single event as a plain text feed line.

    Args:
        event: The Event instance.
        date_prefix: Optional date string to prepend.

    Returns:
        Formatted feed line string.
    """
    venue_name = event.venue.name if event.venue else "TBA"
    parts: list[str] = []

    artist_str = ", ".join(event.artists) if event.artists else event.title

    if date_prefix:
        parts.append(f"{date_prefix}: {artist_str} @ {venue_name}")
    else:
        parts.append(f"{artist_str} @ {venue_name}")

    if event.doors_at:
        parts.append(f"Doors {event.doors_at.strftime('%I:%M %p')}")

    if event.min_price is not None:
        parts.append(f"From ${event.min_price:.0f}")

    parts.append(event.status.value)

    return "• " + " — ".join(parts)
