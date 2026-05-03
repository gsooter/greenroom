"""Repository tests for :mod:`backend.data.repositories.regions`.

Runs against a real PostgreSQL ``greenroom_test`` database using the
transactional fixture in ``conftest.py``. The migrations seed the
``dmv`` row before tests run, so each scenario is a thin wrapper that
asks "does the helper round-trip what's already there?".
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.region import Region
from backend.data.repositories import regions as regions_repo


def test_get_region_by_slug_returns_dmv(session: Session) -> None:
    """The DMV region seeded by the migration is queryable by slug."""
    region = regions_repo.get_region_by_slug(session, "dmv")
    assert region is not None
    assert region.slug == "dmv"
    assert region.display_name == "DMV"


def test_get_region_by_slug_returns_none_for_unknown(session: Session) -> None:
    """An unknown slug returns ``None`` rather than raising."""
    assert regions_repo.get_region_by_slug(session, "atlantis") is None


def test_get_region_by_id_round_trip(
    session: Session, make_region: Callable[..., Region]
) -> None:
    """A freshly created region can be fetched back by primary key."""
    created = make_region(slug=f"r-{uuid.uuid4().hex[:6]}", display_name="Test")
    fetched = regions_repo.get_region_by_id(session, created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_get_region_for_city_returns_dmv_for_dmv_city(
    session: Session, make_city: Callable[..., City]
) -> None:
    """A city created in the seeded DMV region resolves to that region."""
    city = make_city(name="Test DC", slug=f"test-dc-{uuid.uuid4().hex[:6]}")
    region = regions_repo.get_region_for_city(session, city.id)
    assert region is not None
    assert region.slug == "dmv"


def test_get_region_for_city_returns_none_for_missing_city(session: Session) -> None:
    """Looking up a non-existent city UUID returns ``None``."""
    assert regions_repo.get_region_for_city(session, uuid.uuid4()) is None


def test_get_region_for_city_resolves_distinct_region(
    session: Session,
    make_region: Callable[..., Region],
    make_city: Callable[..., City],
) -> None:
    """A city in a different region resolves to that distinct region.

    Guards against the overlay accidentally mapping every city to DMV
    once multi-market expansion lands.
    """
    nyc = make_region(slug=f"nyc-{uuid.uuid4().hex[:6]}", display_name="NYC")
    city = make_city(
        name="NYC City",
        slug=f"nyc-city-{uuid.uuid4().hex[:6]}",
        region_obj=nyc,
    )
    region = regions_repo.get_region_for_city(session, city.id)
    assert region is not None
    assert region.id == nyc.id


def test_get_cities_in_region_returns_active_cities_only(
    session: Session,
    make_region: Callable[..., Region],
    make_city: Callable[..., City],
) -> None:
    """Helper returns active cities in the region, ordered by name."""
    region = make_region(slug=f"test-{uuid.uuid4().hex[:6]}", display_name="TEST")
    make_city(
        name="Bravo",
        slug=f"bravo-{uuid.uuid4().hex[:6]}",
        region_obj=region,
    )
    make_city(
        name="Alpha",
        slug=f"alpha-{uuid.uuid4().hex[:6]}",
        region_obj=region,
    )
    make_city(
        name="Inactive",
        slug=f"inactive-{uuid.uuid4().hex[:6]}",
        region_obj=region,
        is_active=False,
    )
    result = regions_repo.get_cities_in_region(session, region.id)
    names = [c.name for c in result]
    assert names == ["Alpha", "Bravo"]


def test_list_regions_includes_seeded_dmv(session: Session) -> None:
    """The seeded DMV row appears in :func:`list_regions`."""
    rows = regions_repo.list_regions(session)
    assert any(r.slug == "dmv" for r in rows)


@pytest.mark.parametrize("slug", ["DMV", "Dmv", "  dmv  "])
def test_get_region_by_slug_is_case_sensitive(session: Session, slug: str) -> None:
    """Slug lookup matches exactly — no case folding, no trim.

    Slugs are stored lowercase by convention; querying with another
    casing should miss rather than silently match. Keeps repo lookup
    behavior obvious to callers.
    """
    assert regions_repo.get_region_by_slug(session, slug) is None
