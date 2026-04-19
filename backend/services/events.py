"""Event business logic — search, filtering, and feed generation.

All event-related business logic lives here. API routes call these
functions and never access the repository layer directly.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError, ValidationError
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.repositories import events as events_repo


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
        "city": {
            "id": str(city.id),
            "name": city.name,
            "slug": city.slug,
            "state": city.state,
            "region": city.region,
        }
        if city is not None
        else None,
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
