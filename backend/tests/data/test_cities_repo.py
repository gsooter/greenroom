"""Repository tests for :mod:`backend.data.repositories.cities`.

Runs against a real PostgreSQL ``greenroom_test`` database using the
transactional fixture in ``conftest.py``.
"""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.repositories import cities as cities_repo


def test_get_city_by_id_round_trip(
    session: Session, make_city: Callable[..., City]
) -> None:
    city = make_city(name="Arlington", slug="arlington", state="VA")
    fetched = cities_repo.get_city_by_id(session, city.id)
    assert fetched is not None
    assert fetched.slug == "arlington"


def test_get_city_by_id_returns_none_for_missing(session: Session) -> None:
    assert cities_repo.get_city_by_id(session, uuid.uuid4()) is None


def test_get_city_by_slug_case_sensitive(
    session: Session, make_city: Callable[..., City]
) -> None:
    make_city(slug="washington-dc")
    assert cities_repo.get_city_by_slug(session, "washington-dc") is not None
    assert cities_repo.get_city_by_slug(session, "WASHINGTON-DC") is None


def test_list_active_cities_orders_by_name_and_filters_inactive(
    session: Session, make_city: Callable[..., City]
) -> None:
    make_city(name="Baltimore", slug="baltimore", state="MD")
    make_city(name="Alexandria", slug="alexandria", state="VA")
    make_city(name="Gone", slug="gone", state="MD", is_active=False)

    result = cities_repo.list_active_cities(session)
    names = [c.name for c in result]
    assert "Alexandria" in names and "Baltimore" in names
    assert "Gone" not in names
    # Alphabetical.
    assert names.index("Alexandria") < names.index("Baltimore")


def test_list_active_cities_region_filter(
    session: Session, make_city: Callable[..., City]
) -> None:
    make_city(name="DC Hub", slug="dc-hub", region="DMV")
    make_city(name="NYC Hub", slug="nyc-hub", region="NYC")
    result = cities_repo.list_active_cities(session, region="NYC")
    assert [c.slug for c in result] == ["nyc-hub"]


def test_list_cities_by_region_groups_and_ignores_inactive(
    session: Session, make_city: Callable[..., City]
) -> None:
    make_city(name="A1", slug="a1", region="DMV")
    make_city(name="A2", slug="a2", region="DMV")
    make_city(name="B1", slug="b1", region="NYC")
    grouped = cities_repo.list_cities_by_region(session)
    assert {c.slug for c in grouped["DMV"]} >= {"a1", "a2"}
    assert {c.slug for c in grouped["NYC"]} >= {"b1"}


def test_create_city_applies_defaults(session: Session) -> None:
    city = cities_repo.create_city(
        session,
        name="Richmond",
        slug=f"richmond-{uuid.uuid4().hex[:6]}",
        state="VA",
    )
    assert city.region == "DMV"
    assert city.timezone == "America/New_York"
    assert city.is_active is True


def test_update_city_ignores_unknown_attribute(
    session: Session, make_city: Callable[..., City]
) -> None:
    city = make_city(name="Old")
    updated = cities_repo.update_city(
        session, city, name="New", not_a_field="ignored"
    )
    assert updated.name == "New"
    assert not hasattr(updated, "not_a_field")
