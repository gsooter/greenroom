"""Repository functions for city database access.

All database queries related to cities are defined here.
No other module should query the cities table directly.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.region import Region


def get_city_by_id(session: Session, city_id: uuid.UUID) -> City | None:
    """Fetch a city by its primary key.

    Args:
        session: Active SQLAlchemy session.
        city_id: UUID of the city to fetch.

    Returns:
        The City if found, otherwise None.
    """
    return session.get(City, city_id)


def get_city_by_slug(session: Session, slug: str) -> City | None:
    """Fetch a city by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The City if found, otherwise None.
    """
    stmt = select(City).where(City.slug == slug)
    return session.execute(stmt).scalar_one_or_none()


def list_active_cities(session: Session, *, region: str | None = None) -> list[City]:
    """Fetch all active cities ordered by name.

    Args:
        session: Active SQLAlchemy session.
        region: Optional region filter (e.g., "DMV").

    Returns:
        List of active City instances.
    """
    stmt = select(City).where(City.is_active.is_(True))
    if region is not None:
        stmt = stmt.where(City.region == region)
    stmt = stmt.order_by(City.name)
    return list(session.execute(stmt).scalars().all())


def list_cities_by_region(session: Session) -> dict[str, list[City]]:
    """Group all active cities by region.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Dictionary mapping region names to lists of cities.
    """
    cities = list_active_cities(session)
    by_region: dict[str, list[City]] = {}
    for city in cities:
        by_region.setdefault(city.region, []).append(city)
    return by_region


def create_city(
    session: Session,
    *,
    name: str,
    slug: str,
    state: str,
    region: str = "DMV",
    region_id: uuid.UUID | None = None,
    timezone: str = "America/New_York",
    description: str | None = None,
) -> City:
    """Create a new city.

    Args:
        session: Active SQLAlchemy session.
        name: Display name of the city.
        slug: URL-safe slug identifier.
        state: US state abbreviation.
        region: Legacy marketing region string. Defaults to "DMV".
            Retained for back-compat with scraper config and UI
            filters until those callers migrate to ``region_id``.
        region_id: Foreign key to the :class:`Region` this city
            belongs to (Decision 061). When ``None``, the function
            resolves the ``dmv`` region by slug — which is the
            single seeded region today, so most callers can omit
            this kwarg and get the right answer.
        timezone: IANA timezone string. Defaults to America/New_York.
        description: Optional description for SEO.

    Returns:
        The newly created City instance.

    Raises:
        RuntimeError: If ``region_id`` is omitted and the ``dmv``
            region is not seeded (this is a migration sanity issue,
            not a runtime error a caller should be expected to
            handle).
    """
    if region_id is None:
        dmv = session.execute(
            select(Region).where(Region.slug == "dmv")
        ).scalar_one_or_none()
        if dmv is None:
            raise RuntimeError(
                "create_city() called without region_id but the 'dmv' "
                "region is not seeded; run migrations before seeding "
                "cities."
            )
        region_id = dmv.id
    city = City(
        name=name,
        slug=slug,
        state=state,
        region=region,
        region_id=region_id,
        timezone=timezone,
        description=description,
    )
    session.add(city)
    session.flush()
    return city


def update_city(
    session: Session,
    city: City,
    **kwargs: str | bool | None,
) -> City:
    """Update a city's attributes.

    Args:
        session: Active SQLAlchemy session.
        city: The City instance to update.
        **kwargs: Attribute names and their new values.

    Returns:
        The updated City instance.
    """
    for key, value in kwargs.items():
        if hasattr(city, key):
            setattr(city, key, value)
    session.flush()
    return city
