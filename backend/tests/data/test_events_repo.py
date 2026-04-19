"""Repository tests for :mod:`backend.data.repositories.events`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.models.venues import Venue
from backend.data.repositories import events as events_repo

# ---------------------------------------------------------------------------
# Event queries
# ---------------------------------------------------------------------------


def test_get_event_by_id_slug_external_id(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(
        venue=venue,
        title="Show",
        slug="unique-slug",
        external_id="ext-1",
        source_platform="ticketmaster",
    )

    assert events_repo.get_event_by_id(session, event.id).slug == "unique-slug"
    assert events_repo.get_event_by_slug(session, "unique-slug").id == event.id
    assert events_repo.get_event_by_slug(session, "missing") is None

    by_ext = events_repo.get_event_by_external_id(session, "ext-1", "ticketmaster")
    assert by_ext is not None and by_ext.id == event.id
    # Platform must match too.
    assert events_repo.get_event_by_external_id(session, "ext-1", "dice") is None


def test_get_event_by_id_missing_returns_none(session: Session) -> None:
    assert events_repo.get_event_by_id(session, uuid.uuid4()) is None


def test_list_events_city_and_region_filters(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    dmv = make_city(region="DMV")
    nyc = make_city(region="NYC")
    dmv_v = make_venue(city=dmv)
    nyc_v = make_venue(city=nyc)
    make_event(venue=dmv_v, title="DMV Show")
    make_event(venue=nyc_v, title="NYC Show")

    rows, total = events_repo.list_events(session, city_id=dmv.id)
    assert total == 1 and rows[0].title == "DMV Show"

    rows, total = events_repo.list_events(session, region="NYC")
    assert total == 1 and rows[0].title == "NYC Show"


def test_list_events_date_range_and_venue_filter(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    v1 = make_venue(city=city)
    v2 = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(venue=v1, starts_at=now + timedelta(days=1), title="Soon")
    make_event(venue=v1, starts_at=now + timedelta(days=30), title="Later")
    make_event(venue=v2, starts_at=now + timedelta(days=2), title="Other V")

    # Venue filter narrows to v1 only.
    rows, total = events_repo.list_events(session, venue_ids=[v1.id])
    assert total == 2
    assert {e.title for e in rows} == {"Soon", "Later"}

    # Date bounds.
    date_to = (now + timedelta(days=5)).date()
    rows, total = events_repo.list_events(session, venue_ids=[v1.id], date_to=date_to)
    assert total == 1 and rows[0].title == "Soon"

    date_from = (now + timedelta(days=10)).date()
    rows, total = events_repo.list_events(
        session, venue_ids=[v1.id], date_from=date_from
    )
    assert total == 1 and rows[0].title == "Later"


def test_list_events_genre_overlap_and_type_and_status(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="Rock", genres=["rock", "indie"])
    make_event(venue=venue, title="Jazz", genres=["jazz"])
    make_event(
        venue=venue,
        title="Comedy",
        event_type=EventType.COMEDY,
        genres=["standup"],
    )
    make_event(
        venue=venue,
        title="Cancelled",
        status=EventStatus.CANCELLED,
        genres=["rock"],
    )

    rows, total = events_repo.list_events(session, genres=["rock"])
    assert {e.title for e in rows} == {"Rock", "Cancelled"}
    assert total == 2

    rows, total = events_repo.list_events(session, event_type=EventType.COMEDY)
    assert total == 1 and rows[0].title == "Comedy"

    rows, total = events_repo.list_events(session, status=EventStatus.CANCELLED)
    assert total == 1 and rows[0].title == "Cancelled"


def test_list_events_per_page_cap_and_ordering(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(venue=venue, starts_at=now + timedelta(days=3), title="C")
    make_event(venue=venue, starts_at=now + timedelta(days=1), title="A")
    make_event(venue=venue, starts_at=now + timedelta(days=2), title="B")

    rows, _ = events_repo.list_events(session, venue_ids=[venue.id])
    assert [e.title for e in rows] == ["A", "B", "C"]

    # per_page clamps to 100 — pass an absurd value and it should still run.
    rows, _ = events_repo.list_events(session, venue_ids=[venue.id], per_page=9999)
    assert len(rows) == 3


def test_list_events_by_venue_upcoming_only(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(venue=venue, starts_at=now - timedelta(days=5), title="Past")
    make_event(venue=venue, starts_at=now + timedelta(days=5), title="Future")

    rows, total = events_repo.list_events_by_venue(session, venue.id)
    assert total == 1 and rows[0].title == "Future"

    rows, total = events_repo.list_events_by_venue(
        session, venue.id, upcoming_only=False
    )
    assert total == 2


def test_list_events_by_artist_ids_overlap(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(
        venue=venue,
        title="Match",
        spotify_artist_ids=["sp1", "sp2"],
        starts_at=now + timedelta(days=2),
    )
    make_event(
        venue=venue,
        title="Other",
        spotify_artist_ids=["spX"],
        starts_at=now + timedelta(days=3),
    )
    make_event(
        venue=venue,
        title="PastMatch",
        spotify_artist_ids=["sp1"],
        starts_at=now - timedelta(days=3),
    )

    rows = events_repo.list_events_by_artist_ids(session, ["sp1", "zzz"])
    titles = [e.title for e in rows]
    assert titles == ["Match"]

    rows_all = events_repo.list_events_by_artist_ids(
        session, ["sp1"], upcoming_only=False
    )
    assert {e.title for e in rows_all} == {"Match", "PastMatch"}


def test_create_and_update_event(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    event = events_repo.create_event(
        session,
        venue_id=venue.id,
        title="Made",
        slug=f"made-{uuid.uuid4().hex[:6]}",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        artists=["Band"],
    )
    assert event.id is not None

    updated = events_repo.update_event(session, event, title="Renamed", x="ig")
    assert updated.title == "Renamed"


def test_count_events_by_venue(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(venue=venue, starts_at=now + timedelta(days=1))
    make_event(venue=venue, starts_at=now + timedelta(days=2))
    make_event(venue=venue, starts_at=now - timedelta(days=2))

    assert events_repo.count_events_by_venue(session, venue.id) == 2
    assert (
        events_repo.count_events_by_venue(session, venue.id, upcoming_only=False) == 3
    )


# ---------------------------------------------------------------------------
# Ticket pricing snapshots
# ---------------------------------------------------------------------------


def test_ticket_snapshot_crud(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)

    s1 = events_repo.create_ticket_snapshot(
        session,
        event_id=event.id,
        source="seatgeek",
        min_price=10.0,
        max_price=90.0,
        average_price=40.0,
        listing_count=3,
    )
    s2 = events_repo.create_ticket_snapshot(
        session,
        event_id=event.id,
        source="seatgeek",
        min_price=15.0,
        max_price=95.0,
    )
    events_repo.create_ticket_snapshot(
        session, event_id=event.id, source="stubhub", min_price=20.0
    )

    assert s1.currency == "USD"
    assert s2.min_price == 15.0

    all_snaps = events_repo.list_ticket_snapshots(session, event.id)
    assert len(all_snaps) == 3

    only_sg = events_repo.list_ticket_snapshots(session, event.id, source="seatgeek")
    assert {s.source for s in only_sg} == {"seatgeek"}
    assert len(only_sg) == 2

    latest = events_repo.get_latest_ticket_snapshot(session, event.id, "seatgeek")
    assert latest is not None
    # The most recently created seatgeek snapshot was s2.
    assert latest.id == s2.id

    assert events_repo.get_latest_ticket_snapshot(session, event.id, "missing") is None
