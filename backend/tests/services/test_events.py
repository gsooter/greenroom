"""Unit tests for :mod:`backend.services.events`.

The event service is a thin layer over the events repository plus pure
serialization/formatting helpers. Tests stub the repository with fakes
so no Postgres is required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.events import EventStatus, EventType
from backend.services import events as events_service


@dataclass
class _FakeCity:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Washington"
    slug: str = "washington-dc"
    state: str = "DC"
    region: str = "DMV"


@dataclass
class _FakeVenue:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "9:30 Club"
    slug: str = "930-club"
    latitude: float | None = 38.9187
    longitude: float | None = -77.0311
    city: _FakeCity | None = field(default_factory=_FakeCity)


@dataclass
class _FakeEvent:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue_id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue: _FakeVenue | None = field(default_factory=_FakeVenue)
    title: str = "Phoebe Bridgers"
    slug: str = "phoebe-bridgers-930-club-2026-05-01-abc123"
    description: str | None = "With Julien Baker"
    event_type: EventType = EventType.CONCERT
    status: EventStatus = EventStatus.CONFIRMED
    starts_at: datetime | None = field(
        default_factory=lambda: datetime(2026, 5, 1, 20, 0, tzinfo=UTC)
    )
    ends_at: datetime | None = None
    doors_at: datetime | None = field(
        default_factory=lambda: datetime(2026, 5, 1, 19, 0, tzinfo=UTC)
    )
    artists: list[str] = field(default_factory=lambda: ["Phoebe Bridgers"])
    genres: list[str] = field(default_factory=lambda: ["indie"])
    spotify_artist_ids: list[str] = field(default_factory=list)
    image_url: str | None = "https://example.test/img.jpg"
    ticket_url: str | None = "https://tickets.test/x"
    min_price: float | None = 35.0
    max_price: float | None = 65.0
    source_url: str | None = "https://source.test"
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 1, tzinfo=UTC)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 2, tzinfo=UTC)
    )


def test_get_event_returns_row_when_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present row is returned verbatim from the service."""
    event = _FakeEvent()
    monkeypatch.setattr(
        events_service.events_repo, "get_event_by_id", lambda _s, _i: event
    )
    assert events_service.get_event(MagicMock(), event.id) is event  # type: ignore[arg-type]


def test_get_event_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing row surfaces as the domain NotFoundError."""
    monkeypatch.setattr(
        events_service.events_repo, "get_event_by_id", lambda _s, _i: None
    )
    with pytest.raises(NotFoundError):
        events_service.get_event(MagicMock(), uuid.uuid4())


def test_get_event_by_slug_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slug lookup raises NotFoundError on miss."""
    monkeypatch.setattr(
        events_service.events_repo, "get_event_by_slug", lambda _s, _v: None
    )
    with pytest.raises(NotFoundError):
        events_service.get_event_by_slug(MagicMock(), "missing-slug")


def test_get_event_by_slug_returns_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _FakeEvent()
    monkeypatch.setattr(
        events_service.events_repo, "get_event_by_slug", lambda _s, _v: event
    )
    assert (
        events_service.get_event_by_slug(MagicMock(), event.slug) is event  # type: ignore[arg-type]
    )


def test_list_events_rejects_oversized_per_page() -> None:
    """per_page over 100 is a domain validation error, not a DB hit."""
    with pytest.raises(ValidationError):
        events_service.list_events(MagicMock(), per_page=101)


def test_list_events_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        events_service.list_events(MagicMock(), event_type="clown-show")


def test_list_events_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        events_service.list_events(MagicMock(), status="canceled-maybe")


def test_list_events_normalizes_enum_strings_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid enum strings coerce to enums and pass through to the repo."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    events_service.list_events(MagicMock(), event_type="CONCERT", status="Confirmed")
    assert captured["event_type"] is EventType.CONCERT
    assert captured["status"] is EventStatus.CONFIRMED


def test_list_events_rejects_negative_price_max() -> None:
    """Negative price ceilings are user errors, not silently coerced."""
    with pytest.raises(ValidationError):
        events_service.list_events(MagicMock(), price_max=-1.0)


def test_list_events_resolves_artist_ids_to_spotify_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``artist_ids`` UUIDs are translated to Spotify IDs for the repo."""

    @dataclass
    class _FakeArtist:
        id: uuid.UUID
        spotify_id: str | None

    enriched_id = uuid.uuid4()
    unenriched_id = uuid.uuid4()
    fake_artists = [
        _FakeArtist(id=enriched_id, spotify_id="sp123"),
        _FakeArtist(id=unenriched_id, spotify_id=None),
    ]
    monkeypatch.setattr(
        events_service.artists_repo,
        "list_artists_by_ids",
        lambda _s, _ids: fake_artists,
    )

    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    events_service.list_events(MagicMock(), artist_ids=[enriched_id, unenriched_id])
    # Unenriched artists are dropped — only the resolved Spotify ID is sent.
    assert captured["spotify_artist_ids"] == ["sp123"]


def test_list_events_artist_ids_with_no_enrichment_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If none of the supplied artists are enriched, the empty list signals
    'match nothing' to the repo rather than silently dropping the filter."""

    @dataclass
    class _FakeArtist:
        id: uuid.UUID
        spotify_id: str | None

    a_id = uuid.uuid4()
    monkeypatch.setattr(
        events_service.artists_repo,
        "list_artists_by_ids",
        lambda _s, _ids: [_FakeArtist(id=a_id, spotify_id=None)],
    )

    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    events_service.list_events(MagicMock(), artist_ids=[a_id])
    assert captured["spotify_artist_ids"] == []


def test_list_events_forwards_new_filters_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """artist_search, price_max, free_only, available_only round-trip to the repo."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    events_service.list_events(
        MagicMock(),
        artist_search="phoebe",
        price_max=50.0,
        free_only=True,
        available_only=True,
    )
    assert captured["artist_search"] == "phoebe"
    assert captured["price_max"] == 50.0
    assert captured["free_only"] is True
    assert captured["available_only"] is True


def test_serialize_event_includes_venue_and_city() -> None:
    """Full serializer emits nested venue + city blocks."""
    event = _FakeEvent()
    payload = events_service.serialize_event(event)  # type: ignore[arg-type]
    assert payload["id"] == str(event.id)
    assert payload["venue"]["name"] == "9:30 Club"
    assert payload["venue"]["city"]["region"] == "DMV"
    assert payload["min_price"] == 35.0


def test_serialize_event_handles_missing_venue() -> None:
    """A detached event (no venue loaded) still serializes."""
    event = _FakeEvent(venue=None)
    payload = events_service.serialize_event(event)  # type: ignore[arg-type]
    assert payload["venue"] is None


def test_serialize_event_summary_is_compact() -> None:
    """Summary drops description, ticket_url, timestamps etc."""
    event = _FakeEvent()
    payload = events_service.serialize_event_summary(event)  # type: ignore[arg-type]
    assert "description" not in payload
    assert "ticket_url" not in payload
    assert payload["venue"]["name"] == "9:30 Club"


def test_serialize_event_with_city_unloaded() -> None:
    """Venue with null city still serializes (returns nested city=None)."""
    event = _FakeEvent(venue=_FakeVenue(city=None))
    payload = events_service.serialize_event(event)  # type: ignore[arg-type]
    assert payload["venue"]["city"] is None


def test_format_event_feed_separates_tonight_and_upcoming() -> None:
    """Feed groups same-day events under TONIGHT, later dates under UPCOMING."""
    today = datetime(2026, 4, 18, 18, 0, tzinfo=UTC)
    tonight = _FakeEvent(starts_at=today + timedelta(hours=1))
    tomorrow = _FakeEvent(
        starts_at=today + timedelta(days=1),
        artists=["Other"],
        title="Other",
    )
    feed = events_service.format_event_feed([tonight, tomorrow], today)
    assert "TONIGHT" in feed
    assert "UPCOMING" in feed
    assert "Phoebe Bridgers" in feed
    assert "Other" in feed


def test_format_event_feed_omits_empty_buckets() -> None:
    """No tonight events → no TONIGHT header."""
    today = datetime(2026, 4, 18, tzinfo=UTC)
    future = _FakeEvent(starts_at=today + timedelta(days=2))
    feed = events_service.format_event_feed([future], today)
    assert "TONIGHT" not in feed
    assert "UPCOMING" in feed


def test_format_feed_line_includes_price_and_doors() -> None:
    """A full-metadata event renders doors time and dollar price."""
    event = _FakeEvent()
    line = events_service._format_feed_line(event)  # type: ignore[arg-type]
    assert "Phoebe Bridgers @ 9:30 Club" in line
    assert "Doors" in line
    assert "From $35" in line
    assert "confirmed" in line


def test_format_feed_line_falls_back_to_title_when_no_artists() -> None:
    """An event with no artists falls back to the event title."""
    event = _FakeEvent(artists=[], min_price=None, doors_at=None)
    line = events_service._format_feed_line(event, date_prefix="Fri May 01")  # type: ignore[arg-type]
    assert "Fri May 01: Phoebe Bridgers @ 9:30 Club" in line
    assert "Doors" not in line
    assert "From $" not in line


def test_format_feed_line_handles_venue_unloaded() -> None:
    """A detached event renders with a TBA venue."""
    event = _FakeEvent(venue=None)
    line = events_service._format_feed_line(event)  # type: ignore[arg-type]
    assert "@ TBA" in line


# ---------------------------------------------------------------------------
# list_tonight_map_events
# ---------------------------------------------------------------------------


def _tonight_event(
    *,
    starts_at: datetime,
    latitude: float | None = 38.9187,
    longitude: float | None = -77.0311,
    title: str = "Phoebe Bridgers",
    genres: list[str] | None = None,
) -> _FakeEvent:
    """Build a tonight-map-shaped event with adjustable coordinates and time.

    Args:
        starts_at: Event start time.
        latitude: Venue latitude; pass ``None`` to test the coord filter.
        longitude: Venue longitude; pass ``None`` to test the coord filter.
        title: Event title.
        genres: Event genres; defaults to ``["indie"]``.

    Returns:
        A populated :class:`_FakeEvent` with a venue that carries coords.
    """
    venue = _FakeVenue(latitude=latitude, longitude=longitude)
    return _FakeEvent(
        title=title,
        starts_at=starts_at,
        venue=venue,
        genres=genres if genres is not None else ["indie"],
    )


def test_list_tonight_map_events_computes_et_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The date window passed to the repo is `today` in ET, not UTC."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    # 2026-04-20 03:30 UTC == 2026-04-19 23:30 ET (still "tonight" in DC).
    now_utc = datetime(2026, 4, 20, 3, 30, tzinfo=UTC)

    events_service.list_tonight_map_events(MagicMock(), now_utc=now_utc)

    from datetime import date

    assert captured["date_from"] == date(2026, 4, 19)
    assert captured["date_to"] == date(2026, 4, 19)
    assert captured["region"] == "DMV"


def test_list_tonight_map_events_drops_events_without_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Venues without lat/lng can't be pinned so they're filtered out."""
    today = datetime(2026, 4, 20, 20, 0, tzinfo=UTC)
    pinned = _tonight_event(starts_at=today, title="Pinned")
    no_lat = _tonight_event(starts_at=today, title="NoLat", latitude=None)
    no_lng = _tonight_event(starts_at=today, title="NoLng", longitude=None)
    no_venue = _FakeEvent(starts_at=today, title="NoVenue", venue=None)

    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([pinned, no_lat, no_lng, no_venue], 4),
    )
    result = events_service.list_tonight_map_events(MagicMock(), now_utc=today)

    assert result["meta"]["count"] == 1
    assert [e["title"] for e in result["data"]] == ["Pinned"]


def test_list_tonight_map_events_serializes_pin_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each row carries the fields the map UI needs — coords, genres, price."""
    today = datetime(2026, 4, 20, 20, 0, tzinfo=UTC)
    event = _tonight_event(starts_at=today, genres=["indie", "rock"])

    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([event], 1),
    )
    payload = events_service.list_tonight_map_events(MagicMock(), now_utc=today)
    row = payload["data"][0]

    assert row["id"] == str(event.id)
    assert row["title"] == "Phoebe Bridgers"
    assert row["slug"] == event.slug
    assert row["starts_at"] == today.isoformat()
    assert row["artists"] == ["Phoebe Bridgers"]
    assert row["genres"] == ["indie", "rock"]
    assert row["image_url"] == event.image_url
    assert row["min_price"] == 35.0
    assert row["venue"] == {
        "id": str(event.venue.id),
        "name": "9:30 Club",
        "slug": "930-club",
        "latitude": 38.9187,
        "longitude": -77.0311,
    }


def test_list_tonight_map_events_forwards_genres_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    today = datetime(2026, 4, 20, 20, 0, tzinfo=UTC)

    events_service.list_tonight_map_events(
        MagicMock(), now_utc=today, genres=["indie", "punk"]
    )
    assert captured["genres"] == ["indie", "punk"]


def test_list_tonight_map_events_meta_includes_date_and_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = datetime(2026, 4, 20, 20, 0, tzinfo=UTC)
    event = _tonight_event(starts_at=today)

    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([event], 1),
    )
    payload = events_service.list_tonight_map_events(MagicMock(), now_utc=today)

    assert payload["meta"] == {"count": 1, "date": "2026-04-20"}


def test_list_tonight_map_events_empty_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day with no events returns an empty envelope, not None."""
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([], 0),
    )
    today = datetime(2026, 4, 20, 20, 0, tzinfo=UTC)
    payload = events_service.list_tonight_map_events(MagicMock(), now_utc=today)

    assert payload == {"data": [], "meta": {"count": 0, "date": "2026-04-20"}}


def test_list_tonight_map_events_default_now_is_utc_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting now_utc uses the real clock; we just check it doesn't crash."""
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([], 0),
    )
    payload = events_service.list_tonight_map_events(MagicMock())
    assert payload["meta"]["count"] == 0
    assert isinstance(payload["meta"]["date"], str)


# ---------------------------------------------------------------------------
# list_events_near
# ---------------------------------------------------------------------------


# Handy landmark coords that sit ~1.5km apart inside DC, used across
# the near-me suite.
_DUPONT = (38.9090, -77.0430)
_U_STREET = (38.9170, -77.0280)
_BALTIMORE_INNER_HARBOR = (39.2850, -76.6100)


def _near_me_event(
    *,
    starts_at: datetime,
    latitude: float | None,
    longitude: float | None,
    title: str = "Show",
    genres: list[str] | None = None,
) -> _FakeEvent:
    """Build a near-me-shaped event with configurable venue coords.

    Args:
        starts_at: Event start time.
        latitude: Venue latitude, or None to simulate a non-geocoded venue.
        longitude: Venue longitude, or None to simulate a non-geocoded venue.
        title: Event title.
        genres: Event genres.

    Returns:
        An :class:`_FakeEvent` whose venue carries the given coords.
    """
    venue = _FakeVenue(latitude=latitude, longitude=longitude)
    return _FakeEvent(
        title=title,
        starts_at=starts_at,
        venue=venue,
        genres=genres if genres is not None else ["indie"],
    )


def test_list_events_near_filters_outside_radius(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events outside radius_km are dropped even if they match the window."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    close = _near_me_event(
        starts_at=today, latitude=_U_STREET[0], longitude=_U_STREET[1], title="Close"
    )
    far = _near_me_event(
        starts_at=today,
        latitude=_BALTIMORE_INNER_HARBOR[0],
        longitude=_BALTIMORE_INNER_HARBOR[1],
        title="Far",
    )
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([close, far], 2),
    )

    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=10.0,
        window="tonight",
        now_utc=today,
    )

    assert [row["title"] for row in result["data"]] == ["Close"]
    assert result["meta"]["count"] == 1


def test_list_events_near_sorts_by_distance_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Results come back nearest-first regardless of repo ordering."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    close = _near_me_event(
        starts_at=today, latitude=_DUPONT[0], longitude=_DUPONT[1], title="Close"
    )
    medium = _near_me_event(
        starts_at=today, latitude=_U_STREET[0], longitude=_U_STREET[1], title="Medium"
    )
    far_but_inside = _near_me_event(
        starts_at=today, latitude=38.8816, longitude=-77.0910, title="Arlington"
    )
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([medium, far_but_inside, close], 3),
    )
    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=20.0,
        window="tonight",
        now_utc=today,
    )
    titles = [row["title"] for row in result["data"]]
    assert titles == ["Close", "Medium", "Arlington"]


def test_list_events_near_includes_distance_km_on_each_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each row carries a numeric distance_km rounded for display."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    row = _near_me_event(starts_at=today, latitude=_U_STREET[0], longitude=_U_STREET[1])
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([row], 1),
    )
    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=5.0,
        window="tonight",
        now_utc=today,
    )
    distance = result["data"][0]["distance_km"]
    assert isinstance(distance, float)
    # Dupont ↔ U Street is ~1.6km — give ourselves 50% wiggle room.
    assert 0.8 < distance < 2.4


def test_list_events_near_drops_events_without_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events with no venue coords cannot have a distance computed."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    pinned = _near_me_event(
        starts_at=today, latitude=_U_STREET[0], longitude=_U_STREET[1], title="Pinned"
    )
    no_lat = _near_me_event(
        starts_at=today, latitude=None, longitude=-77.0, title="NoLat"
    )
    no_venue = _FakeEvent(starts_at=today, venue=None, title="NoVenue")
    monkeypatch.setattr(
        events_service.events_repo,
        "list_events",
        lambda _s, **_k: ([pinned, no_lat, no_venue], 3),
    )
    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=10.0,
        window="tonight",
        now_utc=today,
    )
    assert [row["title"] for row in result["data"]] == ["Pinned"]


def test_list_events_near_tonight_window_is_et_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `tonight` window uses ET-today even when UTC has crossed midnight."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    # 2026-04-23 03:00 UTC == 2026-04-22 23:00 ET.
    now_utc = datetime(2026, 4, 23, 3, 0, tzinfo=UTC)

    events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=10.0,
        window="tonight",
        now_utc=now_utc,
    )

    from datetime import date

    assert captured["date_from"] == date(2026, 4, 22)
    assert captured["date_to"] == date(2026, 4, 22)


def test_list_events_near_week_window_covers_seven_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `week` window stretches the upper bound six days past today."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_service.events_repo, "list_events", fake_list)
    now_utc = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)

    events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=10.0,
        window="week",
        now_utc=now_utc,
    )

    from datetime import date

    assert captured["date_from"] == date(2026, 4, 22)
    assert captured["date_to"] == date(2026, 4, 28)


def test_list_events_near_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More candidates than `limit` still return only `limit` rows."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    rows = [
        _near_me_event(
            starts_at=today,
            latitude=_DUPONT[0] + 0.001 * i,
            longitude=_DUPONT[1],
            title=f"E{i}",
        )
        for i in range(10)
    ]
    monkeypatch.setattr(
        events_service.events_repo, "list_events", lambda _s, **_k: (rows, len(rows))
    )
    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=50.0,
        window="tonight",
        limit=3,
        now_utc=today,
    )
    assert len(result["data"]) == 3


def test_list_events_near_rejects_invalid_window() -> None:
    """An unknown window string surfaces as a ValidationError."""
    with pytest.raises(ValidationError):
        events_service.list_events_near(
            MagicMock(),
            latitude=_DUPONT[0],
            longitude=_DUPONT[1],
            radius_km=10.0,
            window="forever",  # type: ignore[arg-type]
        )


def test_list_events_near_meta_echoes_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Meta echoes the caller's center, radius, window, and date bounds."""
    today = datetime(2026, 4, 22, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(
        events_service.events_repo, "list_events", lambda _s, **_k: ([], 0)
    )
    result = events_service.list_events_near(
        MagicMock(),
        latitude=_DUPONT[0],
        longitude=_DUPONT[1],
        radius_km=7.5,
        window="tonight",
        now_utc=today,
    )
    meta = result["meta"]
    assert meta["count"] == 0
    assert meta["center"] == {"latitude": _DUPONT[0], "longitude": _DUPONT[1]}
    assert meta["radius_km"] == 7.5
    assert meta["window"] == "tonight"
    assert meta["date_from"] == "2026-04-22"
    assert meta["date_to"] == "2026-04-22"
