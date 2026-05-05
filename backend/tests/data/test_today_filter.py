"""Tests for the timezone-aware ``today`` events filter.

The bug: previously the events listing computed ``date.today()`` from
the server clock, so a user in PT at 11pm local would be told
"nothing today" when it was already past midnight in UTC. This module
covers the new ``today=True`` flag that resolves the day boundary in
the user's timezone.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.events import Event
from backend.data.models.venues import Venue
from backend.services import events as events_service


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a timezone-aware datetime in America/New_York."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_today_filter_returns_events_in_users_local_day(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    in_window = make_event(venue=venue, slug="ev-today", starts_at=_et(2026, 5, 5, 20))
    make_event(venue=venue, slug="ev-tomorrow", starts_at=_et(2026, 5, 6, 20))

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/New_York",
        now_utc=_et(2026, 5, 5, 14).astimezone(UTC),
    )
    ids = [e.id for e in events]
    assert in_window.id in ids
    assert total == 1


def test_today_filter_at_late_evening_keeps_remaining_today(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """11:30 PM ET: a 11:55 PM ET show is still today; midnight+30 is tomorrow."""
    city = make_city()
    venue = make_venue(city=city)
    late_today = make_event(
        venue=venue, slug="ev-late-today", starts_at=_et(2026, 5, 5, 23, 55)
    )
    make_event(venue=venue, slug="ev-just-tomorrow", starts_at=_et(2026, 5, 6, 0, 30))

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/New_York",
        now_utc=_et(2026, 5, 5, 23, 30).astimezone(UTC),
    )
    ids = [e.id for e in events]
    assert late_today.id in ids
    assert total == 1


def test_today_filter_just_after_midnight_keeps_today_not_yesterday(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """12:30 AM ET: a 11:55 PM yesterday show is excluded; tonight's show counts."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, slug="ev-yesterday", starts_at=_et(2026, 5, 4, 23, 55))
    tonight = make_event(venue=venue, slug="ev-tonight", starts_at=_et(2026, 5, 5, 21))

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/New_York",
        now_utc=_et(2026, 5, 5, 0, 30).astimezone(UTC),
    )
    ids = [e.id for e in events]
    assert tonight.id in ids
    assert total == 1


def test_today_filter_defaults_to_eastern_time_for_anonymous(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """No timezone supplied → America/New_York used."""
    city = make_city()
    venue = make_venue(city=city)
    et_today = make_event(
        venue=venue, slug="ev-et-today", starts_at=_et(2026, 5, 5, 23, 30)
    )

    # 11:50 PM ET = 03:50 UTC May 6. Without TZ awareness the filter
    # would compute date.today() == May 6 and miss the May 5 show.
    events, total = events_service.list_events(
        session,
        today=True,
        now_utc=_et(2026, 5, 5, 23, 50).astimezone(UTC),
    )
    ids = [e.id for e in events]
    assert et_today.id in ids
    assert total == 1


def test_today_filter_uses_explicit_timezone_over_default(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """An LA caller's "today" is offset from ET by three hours."""
    city = make_city()
    venue = make_venue(city=city)
    pt = ZoneInfo("America/Los_Angeles")

    # 11:45 PM PT on May 5 → 02:45 UTC May 6 → 10:45 PM ET May 5.
    # An LA show at 11:00 PM PT is "tonight" for an LA caller.
    pt_show_tonight = Event(
        venue_id=venue.id,
        title="LA Show Tonight",
        slug="la-tonight",
        starts_at=datetime(2026, 5, 5, 23, 0, tzinfo=pt),
        artists=[],
    )
    session.add(pt_show_tonight)
    session.flush()

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/Los_Angeles",
        now_utc=datetime(2026, 5, 5, 23, 30, tzinfo=pt).astimezone(UTC),
    )
    assert pt_show_tonight.id in [e.id for e in events]
    assert total == 1


def test_today_filter_rejects_invalid_timezone_name(
    session: Session,
) -> None:
    """A bogus zone name surfaces a clear error rather than falling back silently."""
    import pytest

    from backend.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        events_service.list_events(
            session,
            today=True,
            timezone_name="Not/A_Zone",
        )


def test_explicit_date_from_overrides_today_flag(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """If the caller supplies date_from explicitly, the today flag is ignored."""
    from datetime import date

    city = make_city()
    venue = make_venue(city=city)
    older = make_event(venue=venue, slug="ev-older", starts_at=_et(2026, 5, 1, 20))

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/New_York",
        date_from=date(2026, 4, 30),
        date_to=date(2026, 5, 3),
        now_utc=_et(2026, 5, 5, 14).astimezone(UTC),
    )
    assert older.id in [e.id for e in events]
    assert total == 1


def test_today_filter_inclusive_of_event_at_local_midnight(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """An event that starts exactly at local midnight counts as today."""
    city = make_city()
    venue = make_venue(city=city)
    midnight_show = make_event(
        venue=venue, slug="ev-midnight", starts_at=_et(2026, 5, 5, 0)
    )

    events, total = events_service.list_events(
        session,
        today=True,
        timezone_name="America/New_York",
        now_utc=_et(2026, 5, 5, 1).astimezone(UTC),
    )
    assert midnight_show.id in [e.id for e in events]
    assert total >= 1


def test_today_window_helper_exposes_utc_bounds(
    session: Session,
) -> None:
    """The helper returns UTC-aware datetimes representing the local day."""
    bounds = events_service.compute_today_utc_window(
        timezone_name="America/New_York",
        now_utc=_et(2026, 5, 5, 14).astimezone(UTC),
    )
    # Local midnight May 5 ET = 04:00 UTC May 5.
    assert bounds.start == datetime(2026, 5, 5, 4, tzinfo=UTC)
    # Next local midnight May 6 ET = 04:00 UTC May 6.
    assert bounds.end == datetime(2026, 5, 6, 4, tzinfo=UTC)
    assert bounds.end - bounds.start == timedelta(hours=24)
