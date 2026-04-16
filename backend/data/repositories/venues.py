"""Repository functions for venue database access.

All database queries related to venues are defined here.
No other module should query the venues table directly.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.data.models.venues import Venue


def get_venue_by_id(session: Session, venue_id: uuid.UUID) -> Venue | None:
    """Fetch a venue by its primary key.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue to fetch.

    Returns:
        The Venue if found, otherwise None.
    """
    return session.get(Venue, venue_id)


def get_venue_by_slug(session: Session, slug: str) -> Venue | None:
    """Fetch a venue by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Venue if found, otherwise None.
    """
    stmt = select(Venue).where(Venue.slug == slug)
    return session.execute(stmt).scalar_one_or_none()


def list_venues_by_city(
    session: Session,
    city_id: uuid.UUID,
    *,
    active_only: bool = True,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Venue], int]:
    """Fetch venues for a city with pagination.

    Args:
        session: Active SQLAlchemy session.
        city_id: UUID of the city to filter by.
        active_only: If True, only return active venues. Defaults to True.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 50.

    Returns:
        Tuple of (venues list, total count for pagination).
    """
    base = select(Venue).where(Venue.city_id == city_id)
    if active_only:
        base = base.where(Venue.is_active.is_(True))

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base
        .order_by(Venue.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    venues = list(session.execute(stmt).scalars().all())
    return venues, total


def get_venue_by_external_id(
    session: Session,
    platform: str,
    external_id: str,
) -> Venue | None:
    """Fetch a venue by its external platform ID.

    Uses the JSONB external_ids field to look up venues by their
    identifier on a specific ticketing platform.

    Args:
        session: Active SQLAlchemy session.
        platform: Platform name key in external_ids (e.g., "ticketmaster").
        external_id: The venue's ID on that platform.

    Returns:
        The Venue if found, otherwise None.
    """
    stmt = select(Venue).where(
        Venue.external_ids[platform].astext == external_id
    )
    return session.execute(stmt).scalar_one_or_none()


def create_venue(
    session: Session,
    *,
    city_id: uuid.UUID,
    name: str,
    slug: str,
    address: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    capacity: int | None = None,
    website_url: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    external_ids: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Venue:
    """Create a new venue.

    Args:
        session: Active SQLAlchemy session.
        city_id: UUID of the city this venue belongs to.
        name: Display name of the venue.
        slug: URL-safe slug identifier.
        address: Street address.
        latitude: GPS latitude coordinate.
        longitude: GPS longitude coordinate.
        capacity: Maximum venue capacity.
        website_url: Official website URL.
        description: Venue description for SEO.
        image_url: Primary image URL.
        external_ids: Platform-to-ID mapping as dict.
        tags: Descriptive tags list.

    Returns:
        The newly created Venue instance.
    """
    venue = Venue(
        city_id=city_id,
        name=name,
        slug=slug,
        address=address,
        latitude=latitude,
        longitude=longitude,
        capacity=capacity,
        website_url=website_url,
        description=description,
        image_url=image_url,
        external_ids=external_ids or {},
        tags=tags or [],
    )
    session.add(venue)
    session.flush()
    return venue


def update_venue(
    session: Session,
    venue: Venue,
    **kwargs: Any,
) -> Venue:
    """Update a venue's attributes.

    Args:
        session: Active SQLAlchemy session.
        venue: The Venue instance to update.
        **kwargs: Attribute names and their new values.

    Returns:
        The updated Venue instance.
    """
    for key, value in kwargs.items():
        if hasattr(venue, key):
            setattr(venue, key, value)
    session.flush()
    return venue
