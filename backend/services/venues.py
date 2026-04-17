"""Venue business logic — listing and detail retrieval.

All venue-related business logic lives here. API routes call these
functions and never access the repository layer directly.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.core.exceptions import NotFoundError, ValidationError
from backend.core.exceptions import VENUE_NOT_FOUND
from backend.data.models.venues import Venue
from backend.data.repositories import venues as venues_repo


def get_venue(session: Session, venue_id: uuid.UUID) -> Venue:
    """Fetch a single venue by ID.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue.

    Returns:
        The Venue instance.

    Raises:
        NotFoundError: If the venue does not exist.
    """
    venue = venues_repo.get_venue_by_id(session, venue_id)
    if venue is None:
        raise NotFoundError(
            code=VENUE_NOT_FOUND,
            message=f"No venue found with id {venue_id}",
        )
    return venue


def get_venue_by_slug(session: Session, slug: str) -> Venue:
    """Fetch a single venue by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Venue instance.

    Raises:
        NotFoundError: If the venue does not exist.
    """
    venue = venues_repo.get_venue_by_slug(session, slug)
    if venue is None:
        raise NotFoundError(
            code=VENUE_NOT_FOUND,
            message=f"No venue found with slug '{slug}'",
        )
    return venue


def list_venues(
    session: Session,
    *,
    city_id: uuid.UUID | None = None,
    region: str | None = None,
    active_only: bool = True,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Venue], int]:
    """List venues with optional city and region filters.

    At least one of `city_id` or `region` must be provided so we don't
    return a global paginated list by accident.

    Args:
        session: Active SQLAlchemy session.
        city_id: UUID of the city to filter by.
        region: Region to filter by (e.g., "DMV").
        active_only: If True, only return active venues.
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (venues list, total count).

    Raises:
        ValidationError: If per_page exceeds 100, or neither city_id
            nor region is provided.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")
    if city_id is None and region is None:
        raise ValidationError("Either city_id or region is required.")

    return venues_repo.list_venues(
        session,
        city_id=city_id,
        region=region,
        active_only=active_only,
        page=page,
        per_page=per_page,
    )


def serialize_venue(venue: Venue) -> dict[str, Any]:
    """Serialize a Venue instance to a JSON-safe dictionary.

    Args:
        venue: The Venue instance to serialize.

    Returns:
        Dictionary representation of the venue.
    """
    return {
        "id": str(venue.id),
        "city_id": str(venue.city_id),
        "city": _serialize_nested_city(venue),
        "name": venue.name,
        "slug": venue.slug,
        "address": venue.address,
        "latitude": venue.latitude,
        "longitude": venue.longitude,
        "capacity": venue.capacity,
        "website_url": venue.website_url,
        "description": venue.description,
        "image_url": venue.image_url,
        "tags": venue.tags or [],
        "is_active": venue.is_active,
        "created_at": venue.created_at.isoformat(),
        "updated_at": venue.updated_at.isoformat(),
    }


def serialize_venue_summary(venue: Venue) -> dict[str, Any]:
    """Serialize a Venue to a compact summary for list views.

    Args:
        venue: The Venue instance to serialize.

    Returns:
        Compact dictionary representation of the venue.
    """
    return {
        "id": str(venue.id),
        "name": venue.name,
        "slug": venue.slug,
        "address": venue.address,
        "image_url": venue.image_url,
        "tags": venue.tags or [],
        "city": _serialize_nested_city(venue),
    }


def _serialize_nested_city(venue: Venue) -> dict[str, Any] | None:
    """Serialize the parent city inline on a venue payload.

    Args:
        venue: The Venue instance.

    Returns:
        Compact city dict, or None if the relationship is not loaded.
    """
    if venue.city is None:
        return None
    return {
        "id": str(venue.city.id),
        "name": venue.city.name,
        "slug": venue.city.slug,
        "state": venue.city.state,
        "region": venue.city.region,
    }
