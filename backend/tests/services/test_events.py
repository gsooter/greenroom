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
