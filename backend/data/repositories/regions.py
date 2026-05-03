"""Repository functions for region database access.

All database queries related to regions are defined here. The
recommendation engine reads from this module via the wrapper helpers
in :mod:`backend.recommendations.overlays.actionability` so the
overlay never touches the ORM directly.

Regions are infrastructure for the actionability overlay (Decision
061) — adding a new market is a row-level INSERT plus a
``cities.region_id`` UPDATE. No API surface is exposed yet; future
sprints can add region pickers in the UI without revisiting this
file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.data.models.cities import City
from backend.data.models.region import Region

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def get_region_by_slug(session: Session, slug: str) -> Region | None:
    """Fetch a region by its URL-safe slug.

    Args:
        session: Active SQLAlchemy session.
        slug: Region slug, e.g. ``"dmv"``.

    Returns:
        The :class:`Region` if found, otherwise ``None``.
    """
    stmt = select(Region).where(Region.slug == slug)
    return session.execute(stmt).scalar_one_or_none()


def get_region_by_id(session: Session, region_id: uuid.UUID) -> Region | None:
    """Fetch a region by its primary key.

    Args:
        session: Active SQLAlchemy session.
        region_id: UUID of the region to fetch.

    Returns:
        The :class:`Region` if found, otherwise ``None``.
    """
    return session.get(Region, region_id)


def get_region_for_city(session: Session, city_id: uuid.UUID) -> Region | None:
    """Resolve the region a given city belongs to.

    Used by the actionability overlay to look up the user's preferred
    region in one shot at the start of a scoring run. Returns ``None``
    when the city does not exist or somehow has no region assigned —
    the post-migration invariant is that every city has a region, but
    callers should still handle the missing case rather than crashing
    a recommendation pass.

    Args:
        session: Active SQLAlchemy session.
        city_id: UUID of the city to look up.

    Returns:
        The :class:`Region` the city belongs to, or ``None`` when the
        city is missing or has no region assigned.
    """
    stmt = (
        select(Region).join(City, City.region_id == Region.id).where(City.id == city_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_cities_in_region(session: Session, region_id: uuid.UUID) -> list[City]:
    """Return every active city in the given region.

    Cities are ordered by name so callers (UI region pages, admin
    tools) get a stable list without having to sort downstream.
    Inactive cities are filtered out — same convention as
    :func:`backend.data.repositories.cities.list_active_cities`.

    Args:
        session: Active SQLAlchemy session.
        region_id: UUID of the region whose cities to list.

    Returns:
        List of active :class:`City` rows belonging to ``region_id``.
    """
    stmt = (
        select(City)
        .where(City.region_id == region_id)
        .where(City.is_active.is_(True))
        .order_by(City.name)
    )
    return list(session.execute(stmt).scalars().all())


def list_regions(session: Session) -> list[Region]:
    """Return every region ordered by display name.

    Today the result has a single row (``DMV``); kept as a list-
    returning function so future-multi-region callers don't need to
    change shape.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Ordered list of :class:`Region` rows.
    """
    stmt = select(Region).order_by(Region.display_name)
    return list(session.execute(stmt).scalars().all())
