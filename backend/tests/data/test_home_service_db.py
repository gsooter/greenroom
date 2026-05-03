"""DB-backed tests for :mod:`backend.services.home`.

Mirror tests in ``tests/services/test_home.py`` cover the unit-level
helpers with mocks; this file exercises the actual SQL composition
against the real Postgres test database so future edits to the query
shape (overlap operator, region join, ordering) get caught here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus
from backend.data.models.region import Region
from backend.data.models.users import User
from backend.data.models.venues import Venue
from backend.services import home as home_service


def test_get_new_since_returns_events_created_after_last_visit(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """Only events with ``created_at > last_home_visit_at`` are surfaced."""
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    user.city_id = city.id
    user.spotify_top_artists = [{"name": "Phoebe Bridgers"}]
    user.last_home_visit_at = datetime(2026, 5, 1, 12, tzinfo=UTC)
    session.flush()

    # Event predates the last visit — should be excluded.
    old_event = make_event(
        venue=venue,
        title="Old Show",
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        artists=["Phoebe Bridgers"],
    )
    old_event.created_at = datetime(2026, 4, 30, tzinfo=UTC)
    session.flush()

    fresh_event = make_event(
        venue=venue,
        title="Fresh Show",
        starts_at=datetime(2026, 6, 5, tzinfo=UTC),
        artists=["Phoebe Bridgers"],
    )
    fresh_event.created_at = datetime(2026, 5, 2, 10, tzinfo=UTC)
    session.flush()

    pinned_now = datetime(2026, 5, 3, 12, tzinfo=UTC)
    result = home_service.get_new_since_last_visit(session, user, now=pinned_now)
    assert [e.id for e in result] == [fresh_event.id]


def test_get_new_since_filters_to_anchor_artists(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """Events without an anchor performer are dropped."""
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    user.city_id = city.id
    user.spotify_top_artists = [{"name": "Phoebe Bridgers"}]
    user.last_home_visit_at = datetime(2026, 5, 1, tzinfo=UTC)
    session.flush()

    matching = make_event(
        venue=venue,
        title="Match",
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        artists=["Phoebe Bridgers"],
    )
    matching.created_at = datetime(2026, 5, 2, tzinfo=UTC)

    unrelated = make_event(
        venue=venue,
        title="Stranger",
        starts_at=datetime(2026, 6, 2, tzinfo=UTC),
        artists=["Some Other Band"],
    )
    unrelated.created_at = datetime(2026, 5, 2, tzinfo=UTC)
    session.flush()

    pinned_now = datetime(2026, 5, 3, tzinfo=UTC)
    result = home_service.get_new_since_last_visit(session, user, now=pinned_now)
    assert [e.id for e in result] == [matching.id]


def test_get_new_since_uses_30_day_fallback_for_first_time_user(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """A null last_home_visit_at falls back to a 30-day window."""
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    user.city_id = city.id
    user.spotify_top_artists = [{"name": "Big Thief"}]
    # Note: last_home_visit_at intentionally left None
    session.flush()

    pinned_now = datetime(2026, 5, 3, tzinfo=UTC)

    in_window = make_event(
        venue=venue,
        title="In Window",
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        artists=["Big Thief"],
    )
    in_window.created_at = pinned_now - timedelta(days=10)

    out_of_window = make_event(
        venue=venue,
        title="Out Of Window",
        starts_at=datetime(2026, 6, 2, tzinfo=UTC),
        artists=["Big Thief"],
    )
    out_of_window.created_at = pinned_now - timedelta(days=45)
    session.flush()

    result = home_service.get_new_since_last_visit(session, user, now=pinned_now)
    assert [e.id for e in result] == [in_window.id]


def test_get_new_since_respects_user_preferred_region(
    session: Session,
    make_region: Callable[..., Region],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """An event in a different region is dropped when the user has a city set."""
    other_region = make_region(
        slug=f"other-{uuid.uuid4().hex[:6]}",
        display_name="OTHER",
    )
    dmv_city = make_city()
    out_of_region_city = make_city(
        slug=f"chi-{uuid.uuid4().hex[:6]}",
        region="OTHER",
        region_obj=other_region,
    )

    home_venue = make_venue(city=dmv_city)
    away_venue = make_venue(city=out_of_region_city)

    user = make_user()
    user.city_id = dmv_city.id
    user.spotify_top_artists = [{"name": "Soccer Mommy"}]
    user.last_home_visit_at = datetime(2026, 5, 1, tzinfo=UTC)
    session.flush()

    home_event = make_event(
        venue=home_venue,
        title="DMV Show",
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        artists=["Soccer Mommy"],
    )
    home_event.created_at = datetime(2026, 5, 2, tzinfo=UTC)

    away_event = make_event(
        venue=away_venue,
        title="Chicago Show",
        starts_at=datetime(2026, 6, 1, tzinfo=UTC),
        artists=["Soccer Mommy"],
    )
    away_event.created_at = datetime(2026, 5, 2, tzinfo=UTC)
    session.flush()

    pinned_now = datetime(2026, 5, 3, tzinfo=UTC)
    result = home_service.get_new_since_last_visit(session, user, now=pinned_now)
    assert [e.id for e in result] == [home_event.id]


def test_update_last_home_visit_at_writes_db_row(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """Round-trip: writing the timestamp persists to the row."""
    user = make_user()
    assert user.last_home_visit_at is None

    pinned = datetime(2026, 5, 3, 18, tzinfo=UTC)
    home_service.update_last_home_visit_at(session, user.id, now=pinned)

    refreshed = session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.last_home_visit_at == pinned


def test_get_new_since_excludes_cancelled_and_past_events(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """Cancelled or past events should never appear in the new-since list."""
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    user.city_id = city.id
    user.spotify_top_artists = [{"name": "Indigo De Souza"}]
    user.last_home_visit_at = datetime(2026, 5, 1, tzinfo=UTC)
    session.flush()

    pinned_now = datetime(2026, 5, 3, 12, tzinfo=UTC)

    upcoming_ok = make_event(
        venue=venue,
        title="Future Confirmed",
        starts_at=pinned_now + timedelta(days=20),
        artists=["Indigo De Souza"],
    )
    upcoming_ok.created_at = datetime(2026, 5, 2, tzinfo=UTC)

    cancelled = make_event(
        venue=venue,
        title="Future But Cancelled",
        starts_at=pinned_now + timedelta(days=10),
        artists=["Indigo De Souza"],
        status=EventStatus.CANCELLED,
    )
    cancelled.created_at = datetime(2026, 5, 2, tzinfo=UTC)

    past = make_event(
        venue=venue,
        title="Already Past",
        starts_at=pinned_now - timedelta(days=1),
        artists=["Indigo De Souza"],
    )
    past.created_at = datetime(2026, 5, 2, tzinfo=UTC)
    session.flush()

    result = home_service.get_new_since_last_visit(session, user, now=pinned_now)
    assert [e.id for e in result] == [upcoming_ok.id]
