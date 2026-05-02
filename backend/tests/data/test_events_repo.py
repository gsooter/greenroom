"""Repository tests for :mod:`backend.data.repositories.events`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.models.users import User
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
    """Genre filter goes through artists.canonical_genres → events.artists.

    Sprint 1C moved the source of truth for event genre off the scraped
    ``events.genres`` column onto the curated ``artists.canonical_genres``
    column. The repo layer fetches matching artist names and overlaps
    them against ``events.artists``.
    """
    city = make_city()
    venue = make_venue(city=city)
    session.add_all(
        [
            Artist(
                name="Rock Band",
                normalized_name="rock band",
                canonical_genres=["Rock", "Indie Rock"],
            ),
            Artist(
                name="Jazz Trio",
                normalized_name="jazz trio",
                canonical_genres=["Jazz"],
            ),
        ]
    )
    session.flush()
    make_event(venue=venue, title="Rock", artists=["Rock Band"])
    make_event(venue=venue, title="Jazz", artists=["Jazz Trio"])
    make_event(
        venue=venue,
        title="Comedy",
        event_type=EventType.COMEDY,
        artists=["Some Comedian"],
    )
    make_event(
        venue=venue,
        title="Cancelled",
        status=EventStatus.CANCELLED,
        artists=["Rock Band"],
    )

    rows, total = events_repo.list_events(session, genres=["Rock"])
    assert {e.title for e in rows} == {"Rock", "Cancelled"}
    assert total == 2

    rows, total = events_repo.list_events(session, event_type=EventType.COMEDY)
    assert total == 1 and rows[0].title == "Comedy"

    rows, total = events_repo.list_events(session, status=EventStatus.CANCELLED)
    assert total == 1 and rows[0].title == "Cancelled"

    # No matching artist rows → empty result, regardless of legacy event genres.
    rows, total = events_repo.list_events(session, genres=["Reggae"])
    assert rows == []
    assert total == 0


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


def test_list_events_filters_by_spotify_artist_ids(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``spotify_artist_ids`` filter overlaps the event array column."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="A", spotify_artist_ids=["sp1"])
    make_event(venue=venue, title="B", spotify_artist_ids=["sp2"])
    make_event(venue=venue, title="C", spotify_artist_ids=None)

    rows, total = events_repo.list_events(session, spotify_artist_ids=["sp1"])
    assert total == 1 and rows[0].title == "A"

    # Empty list short-circuits to zero results so callers can pass an
    # empty "follows" list without accidentally widening the query.
    rows, total = events_repo.list_events(session, spotify_artist_ids=[])
    assert total == 0


def test_list_events_artist_search_substring(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Substring search hits any element of the ``artists`` array."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="A", artists=["Phoebe Bridgers", "Julien Baker"])
    make_event(venue=venue, title="B", artists=["MUNA"])
    make_event(venue=venue, title="C", artists=[])

    rows, total = events_repo.list_events(session, artist_search="phoebe")
    assert total == 1 and rows[0].title == "A"

    rows, total = events_repo.list_events(session, artist_search="muna")
    assert total == 1 and rows[0].title == "B"

    # Whitespace-only search is treated as no filter.
    rows, total = events_repo.list_events(session, artist_search="   ")
    assert total == 3


def test_list_events_price_max_excludes_unpriced(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``price_max`` filters by ``min_price`` and drops null-priced rows."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="Cheap", min_price=15.0)
    make_event(venue=venue, title="Mid", min_price=35.0)
    make_event(venue=venue, title="Expensive", min_price=120.0)
    make_event(venue=venue, title="Unpriced", min_price=None)

    rows, total = events_repo.list_events(session, price_max=50.0)
    assert total == 2
    assert {e.title for e in rows} == {"Cheap", "Mid"}


def test_list_events_free_only_zero_price(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``free_only`` matches exactly ``min_price = 0``."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="Free", min_price=0.0)
    make_event(venue=venue, title="Paid", min_price=10.0)
    make_event(venue=venue, title="Unpriced", min_price=None)

    rows, total = events_repo.list_events(session, free_only=True)
    assert total == 1 and rows[0].title == "Free"


def test_list_events_available_only_drops_terminal_states(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``available_only`` excludes cancelled/sold-out/past events."""
    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, title="OK", status=EventStatus.CONFIRMED)
    make_event(venue=venue, title="Postponed", status=EventStatus.POSTPONED)
    make_event(venue=venue, title="Cancelled", status=EventStatus.CANCELLED)
    make_event(venue=venue, title="SoldOut", status=EventStatus.SOLD_OUT)
    make_event(venue=venue, title="Past", status=EventStatus.PAST)

    rows, total = events_repo.list_events(session, available_only=True)
    assert total == 2
    assert {e.title for e in rows} == {"OK", "Postponed"}

    # Explicit status= takes precedence over available_only.
    rows, total = events_repo.list_events(
        session, available_only=True, status=EventStatus.CANCELLED
    )
    assert total == 1 and rows[0].title == "Cancelled"


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


def test_list_latest_snapshots_by_source_returns_one_per_source(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Two snapshots per source → only the newest of each comes back.

    Powers the provider-list rendering on the event detail page; the UI
    needs one row per source, not the full history.
    """
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)

    older_seatgeek = events_repo.create_ticket_snapshot(
        session, event_id=event.id, source="seatgeek", min_price=40.0
    )
    older_seatgeek.created_at = datetime.now(UTC) - timedelta(hours=2)
    newer_seatgeek = events_repo.create_ticket_snapshot(
        session, event_id=event.id, source="seatgeek", min_price=35.0
    )
    newer_seatgeek.created_at = datetime.now(UTC)
    tickpick = events_repo.create_ticket_snapshot(
        session, event_id=event.id, source="tickpick", min_price=30.0
    )
    session.flush()

    rows = events_repo.list_latest_snapshots_by_source(session, event.id)

    by_source = {row.source: row for row in rows}
    assert set(by_source) == {"seatgeek", "tickpick"}
    assert by_source["seatgeek"].id == newer_seatgeek.id
    assert by_source["tickpick"].id == tickpick.id


def test_upsert_pricing_link_inserts_then_updates(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """First call inserts; second call to the same (event, source) updates."""
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)

    inserted = events_repo.upsert_pricing_link(
        session,
        event_id=event.id,
        source="seatgeek",
        url="https://seatgeek.com/x",
        affiliate_url="https://seatgeek.com/x?aid=42",
        is_active=True,
    )
    assert inserted.url == "https://seatgeek.com/x"
    assert inserted.is_active is True
    assert inserted.last_active_at is not None
    first_seen = inserted.last_seen_at

    # Second call updates the same row in-place — no duplicate insert.
    updated = events_repo.upsert_pricing_link(
        session,
        event_id=event.id,
        source="seatgeek",
        url="https://seatgeek.com/y",
        affiliate_url=None,
        is_active=False,
    )
    assert updated.id == inserted.id
    assert updated.url == "https://seatgeek.com/y"
    assert updated.affiliate_url is None
    assert updated.is_active is False
    # last_seen always advances; last_active_at sticks at the most recent
    # active sweep so the UI can still surface the URL when inventory comes
    # back.
    assert updated.last_seen_at >= first_seen
    assert updated.last_active_at == inserted.last_active_at


def test_list_pricing_links_filters_by_active(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """only_active=True drops links whose latest sweep was empty."""
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)

    events_repo.upsert_pricing_link(
        session,
        event_id=event.id,
        source="seatgeek",
        url="https://seatgeek.com/x",
        is_active=True,
    )
    events_repo.upsert_pricing_link(
        session,
        event_id=event.id,
        source="tickpick",
        url="https://tickpick.com/x",
        is_active=False,
    )

    all_links = events_repo.list_pricing_links(session, event.id)
    active_links = events_repo.list_pricing_links(session, event.id, only_active=True)

    assert {link.source for link in all_links} == {"seatgeek", "tickpick"}
    assert {link.source for link in active_links} == {"seatgeek"}


def test_stamp_prices_refreshed_at_writes_event_column(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """The cooldown gate reads what this helper writes."""
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)
    assert event.prices_refreshed_at is None

    stamp = datetime.now(UTC)
    written = events_repo.stamp_prices_refreshed_at(
        session, event.id, refreshed_at=stamp
    )

    assert written == stamp
    session.refresh(event)
    assert event.prices_refreshed_at == stamp


def test_stamp_prices_refreshed_at_is_no_op_for_missing_event(
    session: Session,
) -> None:
    """A missing event id returns the timestamp without raising.

    Lets the service layer call this helper unconditionally — the only
    path to a missing id is a race against a deletion, which is rare
    enough not to deserve its own exception.
    """
    written = events_repo.stamp_prices_refreshed_at(session, uuid.uuid4())
    assert isinstance(written, datetime)


# ---------------------------------------------------------------------------
# list_events_for_pricing_sweep
# ---------------------------------------------------------------------------


def test_list_events_for_pricing_sweep_orders_stalest_first(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Never-refreshed events come first, then oldest refresh, then date.

    Fairness in the sweep: a brand-new event has zero pricing history,
    so giving it priority over re-priced ones means a sold-out
    inventory check still happens for shows that just landed.
    """
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)

    fresh = make_event(
        venue=venue,
        slug="fresh",
        starts_at=now + timedelta(days=10),
    )
    fresh.prices_refreshed_at = now - timedelta(minutes=10)

    stale = make_event(
        venue=venue,
        slug="stale",
        starts_at=now + timedelta(days=20),
    )
    stale.prices_refreshed_at = now - timedelta(hours=20)

    make_event(
        venue=venue,
        slug="never",
        starts_at=now + timedelta(days=30),
    )
    session.flush()

    rows = events_repo.list_events_for_pricing_sweep(session, now=now)

    assert [e.slug for e in rows] == ["never", "stale", "fresh"]


def test_list_events_for_pricing_sweep_excludes_past_events(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Events whose ``starts_at`` is before ``now`` are not swept.

    Once a show has happened the price doesn't matter; including past
    events in the sweep would burn API budget on dead inventory.
    """
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)

    make_event(
        venue=venue,
        slug="past",
        starts_at=now - timedelta(days=1),
    )
    make_event(
        venue=venue,
        slug="upcoming",
        starts_at=now + timedelta(days=5),
    )
    session.flush()

    rows = events_repo.list_events_for_pricing_sweep(session, now=now)
    assert [e.slug for e in rows] == ["upcoming"]


def test_list_events_for_pricing_sweep_respects_limit(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """The limit caps the batch size for the sweep run.

    The cron processes one bounded batch per run; the next morning's
    run picks up what's left because it re-orders by stalest-first.
    """
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)

    for i in range(5):
        make_event(
            venue=venue,
            slug=f"e{i}",
            starts_at=now + timedelta(days=i + 1),
        )
    session.flush()

    rows = events_repo.list_events_for_pricing_sweep(session, now=now, limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# get_latest_pricing_refresh
# ---------------------------------------------------------------------------


def test_get_latest_pricing_refresh_returns_max_across_upcoming(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """The freshness anchor is the most recent stamp across all upcoming events.

    Listing pages render a single "Pricing updated X ago" banner; the
    user wants the *most recent* sweep timestamp, not the oldest.
    """
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)

    older = make_event(venue=venue, slug="older", starts_at=now + timedelta(days=5))
    older.prices_refreshed_at = now - timedelta(hours=12)

    newer = make_event(venue=venue, slug="newer", starts_at=now + timedelta(days=10))
    newer.prices_refreshed_at = now - timedelta(minutes=30)
    session.flush()

    latest = events_repo.get_latest_pricing_refresh(session, now=now)
    assert latest == newer.prices_refreshed_at


def test_get_latest_pricing_refresh_returns_none_when_never_swept(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Brand-new catalogues (no sweep has run yet) return None.

    The frontend renders "never" in that case rather than a misleading
    "just now" or hidden banner.
    """
    city = make_city()
    venue = make_venue(city=city)
    make_event(
        venue=venue, slug="unrefreshed", starts_at=datetime.now(UTC) + timedelta(days=3)
    )
    session.flush()

    assert events_repo.get_latest_pricing_refresh(session) is None


def test_get_latest_pricing_refresh_excludes_past_events(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """A stale stamp on a finished show shouldn't anchor the banner.

    If only past events have any pricing history, treat the catalogue
    as never refreshed — the banner is about *upcoming* listings.
    """
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)

    past = make_event(venue=venue, slug="past", starts_at=now - timedelta(days=1))
    past.prices_refreshed_at = now - timedelta(hours=2)
    session.flush()

    assert events_repo.get_latest_pricing_refresh(session, now=now) is None


# ---------------------------------------------------------------------------
# list_all_event_artist_names
# ---------------------------------------------------------------------------


def test_list_all_event_artist_names_flattens_and_dedupes(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Every distinct raw name across every event row appears once."""
    city = make_city()
    venue = make_venue(city=city)
    event_a = make_event(venue=venue, slug="a")
    event_a.artists = ["Phoebe Bridgers", "Julien Baker"]
    event_b = make_event(venue=venue, slug="b")
    # Duplicate of A's Phoebe keeps the first-seen row's casing.
    event_b.artists = ["Phoebe Bridgers", "Lucy Dacus"]
    event_c = make_event(venue=venue, slug="c")
    event_c.artists = []  # empty arrays don't blow up the scan
    session.flush()

    names = events_repo.list_all_event_artist_names(session)
    assert set(names) == {"Phoebe Bridgers", "Julien Baker", "Lucy Dacus"}


def test_list_all_event_artist_names_skips_blank_and_non_string(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """Whitespace-only names drop, non-string payload fragments drop too."""
    city = make_city()
    venue = make_venue(city=city)
    event = make_event(venue=venue)
    event.artists = ["  Phoebe  ", "", "   "]
    session.flush()

    names = events_repo.list_all_event_artist_names(session)
    assert names == ["Phoebe"]


# ---------------------------------------------------------------------------
# for_you sort
# ---------------------------------------------------------------------------


def _add_recommendation(
    session: Session,
    *,
    user_id: uuid.UUID,
    event_id: uuid.UUID,
    score: float,
) -> None:
    """Insert a Recommendation row for the for_you sort tests.

    Args:
        session: Active SQLAlchemy session.
        user_id: Greenroom user UUID.
        event_id: Event UUID being scored.
        score: Persisted score; higher sorts first.
    """
    from backend.data.models.recommendations import Recommendation

    session.add(
        Recommendation(
            user_id=user_id,
            event_id=event_id,
            score=score,
            score_breakdown={},
        )
    )
    session.flush()


def test_list_events_for_you_sort_orders_by_recommendation_score(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """``sort='for_you'`` ranks scored events ahead of date order."""
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    now = datetime.now(UTC)
    sooner_unscored = make_event(
        venue=venue, title="Sooner", starts_at=now + timedelta(days=1)
    )
    later_strong = make_event(
        venue=venue, title="Strong", starts_at=now + timedelta(days=10)
    )
    middle_weak = make_event(
        venue=venue, title="Weak", starts_at=now + timedelta(days=5)
    )
    _add_recommendation(session, user_id=user.id, event_id=later_strong.id, score=0.95)
    _add_recommendation(session, user_id=user.id, event_id=middle_weak.id, score=0.30)

    rows, total = events_repo.list_events(session, sort="for_you", user_id=user.id)
    assert total == 3
    assert [e.title for e in rows] == ["Strong", "Weak", "Sooner"]
    # Sanity: the unscored event still sorts last by date among unscored.
    assert rows[-1].id == sooner_unscored.id


def test_list_events_for_you_sort_uses_only_callers_recommendations(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
    make_user: Callable[..., User],
) -> None:
    """A different user's recs must not influence the caller's ranking."""
    city = make_city()
    venue = make_venue(city=city)
    me = make_user()
    other = make_user()
    now = datetime.now(UTC)
    make_event(venue=venue, title="A", starts_at=now + timedelta(days=2))
    b = make_event(venue=venue, title="B", starts_at=now + timedelta(days=4))
    # Other user loves B; I have no recs at all.
    _add_recommendation(session, user_id=other.id, event_id=b.id, score=0.99)

    rows, _ = events_repo.list_events(session, sort="for_you", user_id=me.id)
    assert [e.title for e in rows] == ["A", "B"]


def test_list_events_filters_by_day_of_week(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``day_of_week`` filters by ET-local weekday (Postgres dow)."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    city = make_city()
    venue = make_venue(city=city)
    # Saturday (dow=6) at 21:00 ET → 02:00 UTC Sunday in winter offset
    saturday = datetime(2026, 5, 2, 21, 0, tzinfo=et).astimezone(UTC)
    monday = datetime(2026, 5, 4, 20, 0, tzinfo=et).astimezone(UTC)
    sat_event = make_event(venue=venue, title="Saturday show", starts_at=saturday)
    mon_event = make_event(venue=venue, title="Monday show", starts_at=monday)

    rows, _ = events_repo.list_events(session, day_of_week=[6])
    assert {e.id for e in rows} == {sat_event.id}

    rows, _ = events_repo.list_events(session, day_of_week=[1])
    assert {e.id for e in rows} == {mon_event.id}


def test_list_events_filters_by_time_of_day(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``time_of_day`` buckets fire on ET-local hour, not UTC."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    city = make_city()
    venue = make_venue(city=city)
    early = make_event(
        venue=venue,
        title="Brunch set",
        starts_at=datetime(2026, 5, 5, 12, 0, tzinfo=et).astimezone(UTC),
    )
    evening = make_event(
        venue=venue,
        title="Dinner show",
        starts_at=datetime(2026, 5, 5, 20, 0, tzinfo=et).astimezone(UTC),
    )
    late = make_event(
        venue=venue,
        title="Late set",
        starts_at=datetime(2026, 5, 5, 23, 30, tzinfo=et).astimezone(UTC),
    )

    rows, _ = events_repo.list_events(session, time_of_day=["evening"])
    assert {e.id for e in rows} == {evening.id}

    rows, _ = events_repo.list_events(session, time_of_day=["early", "late"])
    assert {e.id for e in rows} == {early.id, late.id}


def test_list_events_has_image_and_has_price(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``has_image`` and ``has_price`` drop unset rows independently."""
    city = make_city()
    venue = make_venue(city=city)
    with_image = make_event(venue=venue, title="With image")
    with_image.image_url = "https://example.test/img.jpg"
    with_price = make_event(venue=venue, title="With price", min_price=25.0)
    bare = make_event(venue=venue, title="Bare")
    session.flush()

    rows, _ = events_repo.list_events(session, has_image=True)
    assert {e.id for e in rows} == {with_image.id}

    rows, _ = events_repo.list_events(session, has_price=True)
    assert {e.id for e in rows} == {with_price.id}

    # Neither filter → all three.
    rows, _ = events_repo.list_events(session)
    assert {e.id for e in rows} == {with_image.id, with_price.id, bare.id}


def test_list_events_for_you_without_user_id_falls_back_to_date(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    """``sort='for_you'`` without ``user_id`` ignores the join."""
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    sooner = make_event(venue=venue, title="Sooner", starts_at=now + timedelta(days=1))
    later = make_event(venue=venue, title="Later", starts_at=now + timedelta(days=5))

    rows, _ = events_repo.list_events(session, sort="for_you")
    assert [e.id for e in rows] == [sooner.id, later.id]
