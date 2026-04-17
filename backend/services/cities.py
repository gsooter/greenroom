"""City business logic — listing and detail retrieval.

All city-related business logic lives here. API routes call these
functions and never access the repository layer directly.
"""

from typing import Any

from sqlalchemy.orm import Session

from backend.core.exceptions import CITY_NOT_FOUND, NotFoundError
from backend.data.models.cities import City
from backend.data.repositories import cities as cities_repo


def list_cities(
    session: Session, *, region: str | None = None
) -> list[City]:
    """List active cities, optionally filtered by region.

    Args:
        session: Active SQLAlchemy session.
        region: Optional region filter (e.g., "DMV").

    Returns:
        List of active City instances.
    """
    return cities_repo.list_active_cities(session, region=region)


def get_city_by_slug(session: Session, slug: str) -> City:
    """Fetch a city by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The City instance.

    Raises:
        NotFoundError: If no city with that slug exists.
    """
    city = cities_repo.get_city_by_slug(session, slug)
    if city is None:
        raise NotFoundError(
            code=CITY_NOT_FOUND,
            message=f"No city found with slug '{slug}'",
        )
    return city


def serialize_city(city: City) -> dict[str, Any]:
    """Serialize a City instance to a JSON-safe dictionary.

    Args:
        city: The City instance to serialize.

    Returns:
        Dictionary representation of the city.
    """
    return {
        "id": str(city.id),
        "name": city.name,
        "slug": city.slug,
        "state": city.state,
        "region": city.region,
        "timezone": city.timezone,
        "description": city.description,
        "is_active": city.is_active,
    }
