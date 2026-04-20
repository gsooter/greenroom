"""Tests for the TicketmasterScraper Discovery API client.

Uses ``responses`` to mock the Discovery v2 endpoint. The scraper is
instantiated with an explicit ``api_key`` so tests never touch the
real environment or ``get_settings``.
"""

from __future__ import annotations

import json
from typing import Any

import responses

from backend.scraper.platforms.ticketmaster import (
    DISCOVERY_API_URL,
    TICKETMASTER_GENRE_MAP,
    TicketmasterScraper,
)


def _event_json(
    *,
    event_id: str = "tm-evt-1",
    name: str = "Black Midi",
    local_date: str = "2026-05-15",
    local_time: str | None = "20:00:00",
    url: str = "https://www.ticketmaster.com/event/tm-evt-1",
    price_ranges: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
    attractions: list[dict[str, Any]] | None = None,
    info: str | None = "Doors 7pm",
    classifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal event dict shaped like the Discovery API response.

    Args:
        event_id: Ticketmaster event ID.
        name: Headline event name.
        local_date: Venue-local YYYY-MM-DD.
        local_time: Venue-local HH:MM:SS, or None to omit.
        url: Detail/ticket URL.
        price_ranges: Override price range entries.
        images: Override image entries.
        attractions: Override attraction entries.
        info: Optional info/description string.

    Returns:
        A dict that matches the shape of a real event node.
    """
    start: dict[str, Any] = {"localDate": local_date}
    if local_time is not None:
        start["localTime"] = local_time
    data: dict[str, Any] = {
        "id": event_id,
        "name": name,
        "url": url,
        "info": info,
        "dates": {"start": start},
    }
    if price_ranges is not None:
        data["priceRanges"] = price_ranges
    if images is not None:
        data["images"] = images
    if attractions is not None:
        data["_embedded"] = {"attractions": attractions}
    if classifications is not None:
        data["classifications"] = classifications
    return data


def _response_json(
    events: list[dict[str, Any]], *, total_pages: int = 1
) -> dict[str, Any]:
    """Wrap a list of event dicts in the Discovery API envelope.

    Args:
        events: List of event dicts to embed in the response.
        total_pages: Value for ``page.totalPages``.

    Returns:
        Dict matching the top-level Discovery API response shape.
    """
    return {
        "_embedded": {"events": events},
        "page": {"totalPages": total_pages},
    }


@responses.activate
def test_parses_events_with_price_image_and_artists() -> None:
    """A fully-populated Discovery event becomes a complete RawEvent."""
    payload = _response_json(
        [
            _event_json(
                price_ranges=[{"min": 25.0, "max": 65.0, "currency": "USD"}],
                images=[
                    {"url": "https://img.tm/small.jpg", "width": 100},
                    {"url": "https://img.tm/large.jpg", "width": 2048},
                ],
                attractions=[{"name": "Black Midi"}, {"name": "Opener Band"}],
            )
        ]
    )
    responses.add(responses.GET, DISCOVERY_API_URL, json=payload, status=200)

    scraper = TicketmasterScraper(
        venue_id="KovZpa2ywe",
        venue_name="9:30 Club",
        api_key="test-key",
    )
    events = list(scraper.scrape())

    assert len(events) == 1
    event = events[0]
    assert event.title == "Black Midi"
    assert event.venue_external_id == "KovZpa2ywe"
    # Naive venue-local datetime — no tzinfo — matches other scrapers.
    assert event.starts_at.isoformat() == "2026-05-15T20:00:00"
    assert event.starts_at.tzinfo is None
    assert event.artists == ["Black Midi", "Opener Band"]
    assert event.min_price == 25.0
    assert event.max_price == 65.0
    assert event.image_url == "https://img.tm/large.jpg"
    assert event.ticket_url == "https://www.ticketmaster.com/event/tm-evt-1"
    assert event.description == "Doors 7pm"
    assert event.raw_data["id"] == "tm-evt-1"


@responses.activate
def test_stops_when_total_pages_reached() -> None:
    """Pagination halts at the API-reported totalPages, not MAX_PAGES."""
    page0 = _response_json([_event_json(event_id="a")], total_pages=2)
    page1 = _response_json([_event_json(event_id="b")], total_pages=2)
    responses.add(responses.GET, DISCOVERY_API_URL, json=page0, status=200)
    responses.add(responses.GET, DISCOVERY_API_URL, json=page1, status=200)

    events = list(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )

    assert [e.raw_data["id"] for e in events] == ["a", "b"]
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limit_retries_then_succeeds(monkeypatch: Any) -> None:
    """On 429 the scraper backs off and retries before yielding results."""
    # Patch sleep so the test doesn't actually wait on backoff.
    monkeypatch.setattr(
        "backend.scraper.platforms.ticketmaster.time.sleep", lambda _: None
    )
    responses.add(responses.GET, DISCOVERY_API_URL, status=429, body="rate limited")
    responses.add(
        responses.GET,
        DISCOVERY_API_URL,
        json=_response_json([_event_json()]),
        status=200,
    )

    events = list(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )

    assert len(events) == 1
    assert len(responses.calls) == 2


@responses.activate
def test_skips_events_with_no_local_date() -> None:
    """An event without ``localDate`` is dropped rather than crashing."""
    bad = _event_json()
    del bad["dates"]["start"]["localDate"]
    payload = _response_json([bad, _event_json(event_id="good")])
    responses.add(responses.GET, DISCOVERY_API_URL, json=payload, status=200)

    events = list(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )

    assert [e.raw_data["id"] for e in events] == ["good"]


@responses.activate
def test_empty_response_ends_scraping_cleanly() -> None:
    """A page with no ``_embedded`` block exits the loop without error."""
    responses.add(responses.GET, DISCOVERY_API_URL, json={"page": {}}, status=200)

    events = list(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )
    assert events == []


@responses.activate
def test_sends_api_key_and_venue_id_as_query_params() -> None:
    """The API key and venueId are wired into the request params."""
    responses.add(
        responses.GET,
        DISCOVERY_API_URL,
        json=_response_json([_event_json()]),
        status=200,
    )

    list(
        TicketmasterScraper(
            venue_id="KovZpa2ywe",
            venue_name="9:30 Club",
            api_key="my-test-key",
        ).scrape()
    )

    assert len(responses.calls) == 1
    request_url = responses.calls[0].request.url
    assert request_url is not None
    assert "apikey=my-test-key" in request_url
    assert "venueId=KovZpa2ywe" in request_url


@responses.activate
def test_extracts_genre_from_classifications() -> None:
    """A standard rock classification yields the mapped canonical genre."""
    payload = _response_json(
        [
            _event_json(
                classifications=[
                    {
                        "primary": True,
                        "segment": {"name": "Music"},
                        "genre": {"name": "Rock"},
                        "subGenre": {"name": "Pop"},
                    }
                ]
            )
        ]
    )
    responses.add(responses.GET, DISCOVERY_API_URL, json=payload, status=200)

    event = next(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )
    assert "rock" in event.genres
    assert "pop" in event.genres


@responses.activate
def test_genres_dedupes_and_skips_unknown() -> None:
    """Unknown genre names are dropped; duplicates collapse to one entry."""
    payload = _response_json(
        [
            _event_json(
                classifications=[
                    {
                        "segment": {"name": "Music"},
                        "genre": {"name": "Rock"},
                        "subGenre": {"name": "Rock"},
                    },
                    {
                        "segment": {"name": "Music"},
                        "genre": {"name": "Undefined"},
                    },
                ]
            )
        ]
    )
    responses.add(responses.GET, DISCOVERY_API_URL, json=payload, status=200)

    event = next(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )
    assert event.genres == ["rock"]


@responses.activate
def test_genres_empty_when_no_classifications() -> None:
    """Events with no classifications come through with empty genres."""
    payload = _response_json([_event_json()])
    responses.add(responses.GET, DISCOVERY_API_URL, json=payload, status=200)

    event = next(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )
    assert event.genres == []


def test_genre_map_covers_common_ticketmaster_genres() -> None:
    """Sanity check: the genre map includes the genres we most often see."""
    for tm_name in ("Rock", "Alternative", "Pop", "Country", "Hip-Hop/Rap"):
        assert tm_name.lower() in TICKETMASTER_GENRE_MAP


@responses.activate
def test_missing_local_time_defaults_to_evening() -> None:
    """An event with no ``localTime`` defaults to 8:00 PM."""
    payload = _response_json([_event_json(local_date="2026-06-01", local_time=None)])
    responses.add(
        responses.GET,
        DISCOVERY_API_URL,
        body=json.dumps(payload),
        content_type="application/json",
        status=200,
    )

    event = next(
        TicketmasterScraper(venue_id="v", venue_name="Test", api_key="k").scrape()
    )
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0
