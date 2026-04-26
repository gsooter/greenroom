"""Tests for :mod:`backend.services.email_structured_data`.

Gmail and Apple Mail render rich actionable cards when an email
embeds Schema.org JSON-LD in the head. The shape of that JSON-LD is
what determines whether the user sees a "View show" pill or a flat
preview, so its correctness is part of the email contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from backend.services import email_structured_data as sd


def _event(**overrides: Any) -> MagicMock:
    """Return a MagicMock Event with the columns we read."""
    venue = MagicMock(name="Venue")
    venue.name = overrides.pop("venue_name", "9:30 Club")
    venue.address = overrides.pop("venue_address", "815 V St NW, Washington, DC 20001")
    venue.latitude = overrides.pop("latitude", 38.917)
    venue.longitude = overrides.pop("longitude", -77.024)

    event = MagicMock(name="Event")
    event.title = overrides.pop("title", "Phoebe Bridgers")
    event.starts_at = overrides.pop(
        "starts_at", datetime(2026, 4, 26, 20, 0, tzinfo=UTC)
    )
    event.ends_at = overrides.pop("ends_at", None)
    event.doors_at = overrides.pop("doors_at", None)
    event.image_url = overrides.pop("image_url", "https://example.com/img.jpg")
    event.min_price = overrides.pop("min_price", 35.0)
    event.max_price = overrides.pop("max_price", 75.0)
    event.ticket_url = overrides.pop("ticket_url", "https://tm.test/show")
    event.artists = overrides.pop(
        "artists", ["Phoebe Bridgers", "Christian Lee Hutson"]
    )
    event.status = overrides.pop("status", MagicMock(value="confirmed"))
    event.venue = venue
    return event


def test_event_to_jsonld_returns_music_event_shape() -> None:
    """The output is a MusicEvent with the schema.org context."""
    blob = sd.event_to_jsonld(
        _event(),
        event_url="https://greenroom.test/events/abc",
    )
    assert blob["@context"] == "https://schema.org"
    assert blob["@type"] == "MusicEvent"
    assert blob["name"] == "Phoebe Bridgers"
    assert blob["url"] == "https://greenroom.test/events/abc"


def test_event_to_jsonld_uses_iso8601_dates() -> None:
    """startDate is the event's starts_at timestamp serialised as ISO 8601."""
    blob = sd.event_to_jsonld(
        _event(starts_at=datetime(2026, 4, 26, 20, 0, tzinfo=UTC)),
        event_url="https://greenroom.test/events/abc",
    )
    assert blob["startDate"] == "2026-04-26T20:00:00+00:00"


def test_event_to_jsonld_includes_venue_location() -> None:
    """The Place object embeds the venue and an address sub-object."""
    blob = sd.event_to_jsonld(
        _event(),
        event_url="https://greenroom.test/events/abc",
    )
    location = blob["location"]
    assert location["@type"] == "MusicVenue"
    assert location["name"] == "9:30 Club"
    address = location["address"]
    assert address["@type"] == "PostalAddress"
    assert "815 V St NW" in address["streetAddress"]


def test_event_to_jsonld_includes_offers_when_priced() -> None:
    """A priced event renders an Offer with the lowest price."""
    blob = sd.event_to_jsonld(
        _event(min_price=35.0, ticket_url="https://tm.test/show"),
        event_url="https://greenroom.test/events/abc",
    )
    offer = blob["offers"]
    assert offer["@type"] == "Offer"
    assert offer["price"] == "35.00"
    assert offer["priceCurrency"] == "USD"
    assert offer["url"] == "https://tm.test/show"


def test_event_to_jsonld_omits_offers_when_unpriced() -> None:
    """No min_price → no offers block."""
    blob = sd.event_to_jsonld(
        _event(min_price=None, max_price=None),
        event_url="https://greenroom.test/events/abc",
    )
    assert "offers" not in blob


def test_event_to_jsonld_includes_performers() -> None:
    """Each artist becomes a MusicGroup performer entry."""
    blob = sd.event_to_jsonld(
        _event(artists=["Phoebe Bridgers", "Christian Lee Hutson"]),
        event_url="https://greenroom.test/events/abc",
    )
    performers = blob["performer"]
    assert isinstance(performers, list)
    assert {p["name"] for p in performers} == {
        "Phoebe Bridgers",
        "Christian Lee Hutson",
    }
    assert all(p["@type"] == "MusicGroup" for p in performers)


def test_event_to_jsonld_maps_status_to_schema_url() -> None:
    """A cancelled event reports the schema.org cancelled URL."""
    blob = sd.event_to_jsonld(
        _event(status=MagicMock(value="cancelled")),
        event_url="https://greenroom.test/events/abc",
    )
    assert blob["eventStatus"] == "https://schema.org/EventCancelled"


def test_event_to_jsonld_includes_image_when_present() -> None:
    """The event image is surfaced as a top-level URL."""
    blob = sd.event_to_jsonld(
        _event(image_url="https://example.com/img.jpg"),
        event_url="https://greenroom.test/events/abc",
    )
    assert blob["image"] == "https://example.com/img.jpg"


def test_event_to_jsonld_omits_image_when_absent() -> None:
    """No image_url → no image key."""
    blob = sd.event_to_jsonld(
        _event(image_url=None),
        event_url="https://greenroom.test/events/abc",
    )
    assert "image" not in blob
