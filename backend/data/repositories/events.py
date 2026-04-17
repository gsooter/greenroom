"""Repository functions for event and ticket pricing database access.

All database queries related to events and ticket pricing snapshots
are defined here. No other module should query these tables directly.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.data.models.events import (
    Event,
    EventStatus,
    EventType,
    TicketPricingSnapshot,
)


# ---------------------------------------------------------------------------
# Event queries
# ---------------------------------------------------------------------------


def get_event_by_id(session: Session, event_id: uuid.UUID) -> Event | None:
    """Fetch an event by its primary key.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event to fetch.

    Returns:
        The Event if found, otherwise None.
    """
    return session.get(Event, event_id)


def get_event_by_slug(session: Session, slug: str) -> Event | None:
    """Fetch an event by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Event if found, otherwise None.
    """
    stmt = select(Event).where(Event.slug == slug)
    return session.execute(stmt).scalar_one_or_none()


def get_event_by_external_id(
    session: Session,
    external_id: str,
    source_platform: str,
) -> Event | None:
    """Fetch an event by its external platform ID.

    Used by scrapers to check if an event already exists before inserting.

    Args:
        session: Active SQLAlchemy session.
        external_id: The event's ID on the source platform.
        source_platform: Name of the source platform.

    Returns:
        The Event if found, otherwise None.
    """
    stmt = select(Event).where(
        Event.external_id == external_id,
        Event.source_platform == source_platform,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_events(
    session: Session,
    *,
    city_id: uuid.UUID | None = None,
    region: str | None = None,
    venue_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    genres: list[str] | None = None,
    event_type: EventType | None = None,
    status: EventStatus | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """Fetch events with optional filters and pagination.

    Args:
        session: Active SQLAlchemy session.
        city_id: Filter to events in venues belonging to this city.
        region: Filter to events in venues in cities with this region
            (e.g., "DMV"). Combined with city_id via AND.
        venue_ids: Filter to specific venues. None means all venues.
        date_from: Start of date range (inclusive). None means no lower bound.
        date_to: End of date range (inclusive). None means no upper bound.
        genres: Filter to events matching any of these genres (overlap).
        event_type: Filter to a specific event type.
        status: Filter to a specific event status.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Maximum 100. Defaults to 20.

    Returns:
        Tuple of (events list, total count for pagination).
    """
    from backend.data.models.cities import City
    from backend.data.models.venues import Venue

    per_page = min(per_page, 100)

    base = select(Event)
    needs_venue_join = city_id is not None or region is not None
    if needs_venue_join:
        base = base.join(Venue, Event.venue_id == Venue.id)

    if city_id is not None:
        base = base.where(Venue.city_id == city_id)

    if region is not None:
        base = base.join(City, Venue.city_id == City.id).where(
            City.region == region
        )

    if venue_ids is not None:
        base = base.where(Event.venue_id.in_(venue_ids))

    if date_from is not None:
        base = base.where(Event.starts_at >= datetime.combine(
            date_from, datetime.min.time()
        ))

    if date_to is not None:
        base = base.where(Event.starts_at <= datetime.combine(
            date_to, datetime.max.time()
        ))

    if genres is not None:
        base = base.where(Event.genres.overlap(genres))

    if event_type is not None:
        base = base.where(Event.event_type == event_type)

    if status is not None:
        base = base.where(Event.status == status)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base
        .order_by(Event.starts_at)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    events = list(session.execute(stmt).scalars().all())
    return events, total


def list_events_by_venue(
    session: Session,
    venue_id: uuid.UUID,
    *,
    upcoming_only: bool = True,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """Fetch events for a specific venue with pagination.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue.
        upcoming_only: If True, only return future events. Defaults to True.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 20.

    Returns:
        Tuple of (events list, total count for pagination).
    """
    base = select(Event).where(Event.venue_id == venue_id)

    if upcoming_only:
        base = base.where(Event.starts_at >= func.now())

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base
        .order_by(Event.starts_at)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    events = list(session.execute(stmt).scalars().all())
    return events, total


def list_events_by_artist_ids(
    session: Session,
    spotify_artist_ids: list[str],
    *,
    upcoming_only: bool = True,
) -> list[Event]:
    """Fetch events matching any of the given Spotify artist IDs.

    Uses the GIN index on spotify_artist_ids for fast overlap queries.
    This is the core query powering the recommendation engine.

    Args:
        session: Active SQLAlchemy session.
        spotify_artist_ids: Spotify artist IDs to match against.
        upcoming_only: If True, only return future events. Defaults to True.

    Returns:
        List of matching Event instances ordered by start date.
    """
    stmt = select(Event).where(
        Event.spotify_artist_ids.overlap(spotify_artist_ids)
    )

    if upcoming_only:
        stmt = stmt.where(Event.starts_at >= func.now())

    stmt = stmt.order_by(Event.starts_at)
    return list(session.execute(stmt).scalars().all())


def create_event(session: Session, **kwargs: Any) -> Event:
    """Create a new event.

    Args:
        session: Active SQLAlchemy session.
        **kwargs: Event attribute names and values. Must include at minimum
            venue_id, title, slug, and starts_at.

    Returns:
        The newly created Event instance.
    """
    event = Event(**kwargs)
    session.add(event)
    session.flush()
    return event


def update_event(
    session: Session,
    event: Event,
    **kwargs: Any,
) -> Event:
    """Update an event's attributes.

    Args:
        session: Active SQLAlchemy session.
        event: The Event instance to update.
        **kwargs: Attribute names and their new values.

    Returns:
        The updated Event instance.
    """
    for key, value in kwargs.items():
        if hasattr(event, key):
            setattr(event, key, value)
    session.flush()
    return event


def count_events_by_venue(
    session: Session,
    venue_id: uuid.UUID,
    *,
    upcoming_only: bool = True,
) -> int:
    """Count events for a venue.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue.
        upcoming_only: If True, only count future events. Defaults to True.

    Returns:
        The number of matching events.
    """
    stmt = select(func.count()).where(Event.venue_id == venue_id)
    if upcoming_only:
        stmt = stmt.where(Event.starts_at >= func.now())
    return session.execute(stmt).scalar_one()


# ---------------------------------------------------------------------------
# Ticket pricing snapshot queries
# ---------------------------------------------------------------------------


def create_ticket_snapshot(
    session: Session,
    *,
    event_id: uuid.UUID,
    source: str,
    min_price: float | None = None,
    max_price: float | None = None,
    average_price: float | None = None,
    listing_count: int | None = None,
    currency: str = "USD",
    raw_data: dict[str, Any] | None = None,
) -> TicketPricingSnapshot:
    """Create a new ticket pricing snapshot.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event this pricing is for.
        source: Platform name (e.g., "seatgeek", "stubhub").
        min_price: Minimum ticket price.
        max_price: Maximum ticket price.
        average_price: Average ticket price.
        listing_count: Number of active listings.
        currency: Currency code. Defaults to USD.
        raw_data: Full pricing payload from the source.

    Returns:
        The newly created TicketPricingSnapshot instance.
    """
    snapshot = TicketPricingSnapshot(
        event_id=event_id,
        source=source,
        min_price=min_price,
        max_price=max_price,
        average_price=average_price,
        listing_count=listing_count,
        currency=currency,
        raw_data=raw_data,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def list_ticket_snapshots(
    session: Session,
    event_id: uuid.UUID,
    *,
    source: str | None = None,
    limit: int = 50,
) -> list[TicketPricingSnapshot]:
    """Fetch ticket pricing snapshots for an event.

    Returns snapshots in reverse chronological order for price
    history and trend display.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        source: Optional platform filter.
        limit: Maximum number of snapshots to return. Defaults to 50.

    Returns:
        List of TicketPricingSnapshot instances, newest first.
    """
    stmt = (
        select(TicketPricingSnapshot)
        .where(TicketPricingSnapshot.event_id == event_id)
    )

    if source is not None:
        stmt = stmt.where(TicketPricingSnapshot.source == source)

    stmt = stmt.order_by(
        TicketPricingSnapshot.created_at.desc()
    ).limit(limit)

    return list(session.execute(stmt).scalars().all())


def get_latest_ticket_snapshot(
    session: Session,
    event_id: uuid.UUID,
    source: str,
) -> TicketPricingSnapshot | None:
    """Fetch the most recent ticket pricing snapshot for an event and source.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        source: Platform name.

    Returns:
        The latest TicketPricingSnapshot if any exist, otherwise None.
    """
    stmt = (
        select(TicketPricingSnapshot)
        .where(
            TicketPricingSnapshot.event_id == event_id,
            TicketPricingSnapshot.source == source,
        )
        .order_by(TicketPricingSnapshot.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()
