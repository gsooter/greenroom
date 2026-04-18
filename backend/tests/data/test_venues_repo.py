"""Repository tests for :mod:`backend.data.repositories.venues`."""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.venues import Venue
from backend.data.repositories import venues as venues_repo


def test_get_venue_by_id_and_slug(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    venue = make_venue(city=city, name="Black Cat", slug="black-cat")

    assert venues_repo.get_venue_by_id(session, venue.id).slug == "black-cat"
    assert venues_repo.get_venue_by_slug(session, "black-cat").id == venue.id
    assert venues_repo.get_venue_by_slug(session, "missing") is None


def test_get_venue_by_id_missing_returns_none(session: Session) -> None:
    assert venues_repo.get_venue_by_id(session, uuid.uuid4()) is None


def test_list_venues_filters_inactive(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    make_venue(city=city, name="Open", slug="open", is_active=True)
    make_venue(city=city, name="Shut", slug="shut", is_active=False)

    venues, total = venues_repo.list_venues(session, city_id=city.id)
    assert total == 1
    assert venues[0].slug == "open"

    venues, total = venues_repo.list_venues(
        session, city_id=city.id, active_only=False
    )
    assert total == 2


def test_list_venues_region_join_filter(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    dmv = make_city(region="DMV")
    nyc = make_city(region="NYC")
    make_venue(city=dmv, slug="dmv-v")
    make_venue(city=nyc, slug="nyc-v")

    venues, total = venues_repo.list_venues(session, region="NYC")
    assert total == 1
    assert venues[0].slug == "nyc-v"


def test_list_venues_ordered_by_name_and_paginated(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    for name in ["Zed", "Alpha", "Mango"]:
        make_venue(city=city, name=name, slug=name.lower())

    page_1, total = venues_repo.list_venues(
        session, city_id=city.id, page=1, per_page=2
    )
    page_2, _ = venues_repo.list_venues(
        session, city_id=city.id, page=2, per_page=2
    )
    assert total == 3
    assert [v.name for v in page_1] == ["Alpha", "Mango"]
    assert [v.name for v in page_2] == ["Zed"]


def test_list_venues_by_city_delegates_correctly(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    make_venue(city=city)
    venues, total = venues_repo.list_venues_by_city(session, city.id)
    assert total == 1
    assert venues[0].city_id == city.id


def test_get_venue_by_external_id_jsonb_lookup(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    make_venue(
        city=city,
        slug="tm-venue",
        external_ids={"ticketmaster": "KovZpa123", "seatgeek": "999"},
    )
    found = venues_repo.get_venue_by_external_id(
        session, "ticketmaster", "KovZpa123"
    )
    assert found is not None and found.slug == "tm-venue"

    miss = venues_repo.get_venue_by_external_id(session, "dice", "KovZpa123")
    assert miss is None


def test_create_venue_persists_optional_fields(
    session: Session, make_city: Callable[..., City]
) -> None:
    city = make_city()
    venue = venues_repo.create_venue(
        session,
        city_id=city.id,
        name="New Room",
        slug=f"new-room-{uuid.uuid4().hex[:6]}",
        address="123 Main",
        latitude=38.9,
        longitude=-77.0,
        capacity=800,
        website_url="https://example.test",
        description="A place.",
        image_url="https://img.test/x.jpg",
        external_ids={"ticketmaster": "abc"},
        tags=["intimate"],
    )
    assert venue.capacity == 800
    assert venue.external_ids == {"ticketmaster": "abc"}
    assert venue.tags == ["intimate"]


def test_create_venue_defaults_external_ids_and_tags(
    session: Session, make_city: Callable[..., City]
) -> None:
    city = make_city()
    venue = venues_repo.create_venue(
        session,
        city_id=city.id,
        name="Bare",
        slug=f"bare-{uuid.uuid4().hex[:6]}",
    )
    assert venue.external_ids == {}
    assert venue.tags == []


def test_update_venue_sets_fields_and_ignores_unknowns(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    updated = venues_repo.update_venue(
        session, venue, name="Renamed", bogus="ignored"
    )
    assert updated.name == "Renamed"
    assert not hasattr(updated, "bogus")
