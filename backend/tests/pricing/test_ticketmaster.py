"""Tests for the Ticketmaster pricing provider.

Covers :class:`backend.pricing.providers.ticketmaster.TicketmasterPricingProvider`.
The provider is a thin HTTP client over the Ticketmaster Discovery API.
Tests mock HTTP with ``responses`` and use light fakes in place of the
ORM :class:`Event` so nothing touches the database or network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import requests
import responses

from backend.pricing.providers.ticketmaster import (
    TICKETMASTER_BASE_URL,
    TicketmasterPricingProvider,
    _as_float,
)


class _FakeVenue:
    """Stand-in for :class:`backend.data.models.venues.Venue`.

    Attributes:
        external_ids: JSONB column the provider reads to find a
            Ticketmaster venue id mapping.
    """

    def __init__(self, external_ids: dict[str, Any] | None = None):
        """Initialize the fake venue.

        Args:
            external_ids: Platform → external id mapping; the provider
                looks up its own ``name`` key here.
        """
        self.external_ids = external_ids


class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Carries only the attributes the provider reads.

    Attributes:
        title: Event headline used as a search-query fallback.
        artists: Performer list; the first entry becomes the keyword.
        starts_at: Event start datetime; drives the date-window filter.
        source_platform: Origin platform of the underlying scrape.
        external_id: External id on the origin platform.
        venue: Fake venue; provider reads ``venue.external_ids``.
    """

    _UNSET: Any = object()

    def __init__(
        self,
        *,
        title: str = "Headliner Show",
        artists: Any = _UNSET,
        starts_at: datetime | None = None,
        source_platform: str | None = None,
        external_id: str | None = None,
        venue: _FakeVenue | None = None,
    ):
        """Initialize the fake event.

        Args:
            title: Event title.
            artists: Performer list. Pass ``[]`` to test the no-artists
                fallback explicitly; omitting supplies a default.
            starts_at: Start datetime; defaults to a fixed UTC date.
            source_platform: Origin platform of the underlying scrape.
            external_id: External id on the origin platform.
            venue: Optional fake venue.
        """
        self.title = title
        if artists is _FakeEvent._UNSET:
            self.artists = ["Headline Artist"]
        else:
            self.artists = artists
        self.starts_at = starts_at or datetime(2026, 5, 1, 20, 0, tzinfo=UTC)
        self.source_platform = source_platform
        self.external_id = external_id
        self.venue = venue or _FakeVenue()


def _make_provider(*, api_key: str = "test-tm") -> TicketmasterPricingProvider:
    """Construct a provider with an explicit api key for tests.

    Args:
        api_key: Ticketmaster API key to send.

    Returns:
        A configured :class:`TicketmasterPricingProvider`.
    """
    return TicketmasterPricingProvider(api_key=api_key)


def _event_payload(
    *,
    price_ranges: list[dict[str, Any]] | None = None,
    status_code: str = "onsale",
    url: str | None = "https://ticketmaster.com/event-1",
) -> dict[str, Any]:
    """Build a Discovery-API-shaped event payload.

    Args:
        price_ranges: ``priceRanges`` array; defaults to a single
            standard tier.
        status_code: ``dates.status.code`` value (drives ``is_active``).
        url: Buy URL on the event payload.

    Returns:
        Dict matching the shape of one Discovery API event entry.
    """
    return {
        "id": "tm-event-1",
        "name": "Some Show",
        "url": url,
        "priceRanges": price_ranges
        or [{"type": "standard", "currency": "USD", "min": 35.0, "max": 90.0}],
        "dates": {"status": {"code": status_code}},
    }


# -------------------------------------------------------------- direct ID path


@responses.activate  # type: ignore[misc]
def test_fetch_uses_direct_id_when_event_sourced_from_ticketmaster() -> None:
    """A TM-origin event hits ``events/{id}.json`` and skips search.

    Events scraped from Ticketmaster carry the canonical id in
    ``external_id``; the provider should fast-path the lookup rather
    than running a wider search.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events/tm-123.json",
        json=_event_payload(),
        status=200,
    )
    event = _FakeEvent(source_platform="ticketmaster", external_id="tm-123")

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert quote.source == "ticketmaster"
    assert quote.min_price == 35.0
    assert quote.max_price == 90.0
    assert quote.currency == "USD"
    assert quote.listing_count is None
    assert quote.buy_url == "https://ticketmaster.com/event-1"
    assert quote.is_active is True


@responses.activate  # type: ignore[misc]
def test_direct_id_404_falls_back_to_search() -> None:
    """A 404 on direct lookup retries via search.

    Stale TM ids do happen — events get re-listed under a new id when
    a tour is rescheduled, and the search fallback is the recovery
    path that lets the link still resolve.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events/old-tm.json",
        json={},
        status=404,
    )
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events.json",
        json={"_embedded": {"events": [_event_payload()]}},
        status=200,
    )
    event = _FakeEvent(source_platform="ticketmaster", external_id="old-tm")

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert quote.min_price == 35.0


# ---------------------------------------------------------------- search path


@responses.activate  # type: ignore[misc]
def test_search_passes_venue_keyword_and_date_window() -> None:
    """The search call includes venueId, keyword, and a ±24h window.

    Without those filters Discovery returns far too broad a candidate
    set for a generic artist name; this test pins the expected
    parameters so the orchestrator gets a focused result.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events.json",
        json={"_embedded": {"events": [_event_payload()]}},
        status=200,
    )
    venue = _FakeVenue(external_ids={"ticketmaster": "K-99"})
    event = _FakeEvent(
        title="Some Tour",
        artists=["Phoebe Bridgers"],
        starts_at=datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        venue=venue,
    )

    quote = _make_provider().fetch(event)

    assert quote is not None
    sent = responses.calls[0].request.url
    assert "venueId=K-99" in sent
    assert "keyword=Phoebe+Bridgers" in sent
    assert "startDateTime=2026-04-30T20%3A00%3A00Z" in sent
    assert "endDateTime=2026-05-02T20%3A00%3A00Z" in sent
    assert "apikey=test-tm" in sent


@responses.activate  # type: ignore[misc]
def test_search_returning_no_events_yields_none() -> None:
    """An empty embedded events array makes the provider abstain.

    Mirrors the SeatGeek behavior — abstaining keeps the link table
    free of "no inventory" rows for events Ticketmaster doesn't carry.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events.json",
        json={"_embedded": {"events": []}},
        status=200,
    )
    assert _make_provider().fetch(_FakeEvent()) is None


@responses.activate  # type: ignore[misc]
def test_search_response_without_embedded_yields_none() -> None:
    """A 200 without ``_embedded`` is treated as no results.

    Discovery omits ``_embedded`` entirely when zero events match,
    rather than returning an empty array; provider must handle both.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events.json",
        json={"page": {"totalElements": 0}},
        status=200,
    )
    assert _make_provider().fetch(_FakeEvent()) is None


# -------------------------------------------------------------- payload parsing


@responses.activate  # type: ignore[misc]
def test_offsale_status_marks_quote_inactive_but_keeps_url() -> None:
    """``dates.status.code == "offsale"`` flips ``is_active`` off.

    Decoupling pricing-link from snapshot relies on this — sold-out
    events still need their buy URL preserved so the next refresh
    that finds inventory can flip activity back on.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events/sold.json",
        json=_event_payload(status_code="offsale"),
        status=200,
    )
    event = _FakeEvent(source_platform="ticketmaster", external_id="sold")
    quote = _make_provider().fetch(event)
    assert quote is not None
    assert quote.is_active is False
    assert quote.buy_url == "https://ticketmaster.com/event-1"


@responses.activate  # type: ignore[misc]
def test_multiple_price_ranges_collapse_to_overall_min_and_max() -> None:
    """Multi-tier ``priceRanges`` (standard + platinum) become one min/max.

    Discovery exposes per-type price tiers; the orchestrator only
    persists a single (min, max) so the provider takes the overall
    extremes across types.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events/tier.json",
        json=_event_payload(
            price_ranges=[
                {"type": "standard", "currency": "USD", "min": 50, "max": 120},
                {"type": "platinum", "currency": "USD", "min": 200, "max": 800},
            ]
        ),
        status=200,
    )
    event = _FakeEvent(source_platform="ticketmaster", external_id="tier")
    quote = _make_provider().fetch(event)
    assert quote is not None
    assert quote.min_price == 50.0
    assert quote.max_price == 800.0


@responses.activate  # type: ignore[misc]
def test_payload_without_url_or_prices_returns_none() -> None:
    """A payload with no URL and no priceRanges abstains.

    Persisting that as a snapshot would be pure noise — UI has nothing
    to render and the future ML layer has no signal.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events/empty.json",
        json={"id": "empty", "name": "Empty"},
        status=200,
    )
    event = _FakeEvent(source_platform="ticketmaster", external_id="empty")
    assert _make_provider().fetch(event) is None


@responses.activate  # type: ignore[misc]
def test_5xx_raises_so_orchestrator_can_back_off() -> None:
    """A 500 from Discovery raises so the orchestrator records failure.

    Transient upstream errors must propagate; swallowing them would
    let the daily sweep silently leave events with stale prices.
    """
    responses.add(
        responses.GET,
        f"{TICKETMASTER_BASE_URL}/events.json",
        json={"error": "boom"},
        status=503,
    )
    with pytest.raises(requests.HTTPError):
        _make_provider().fetch(_FakeEvent())


def test_as_float_handles_none_and_string_numbers() -> None:
    """The numeric coercion preserves ``None`` and accepts strings.

    Discovery sometimes serialises prices as strings; the helper
    keeps the persistence layer's float column consistent regardless.
    """
    assert _as_float(None) is None
    assert _as_float("not-a-number") is None
    assert _as_float(35) == 35.0
    assert _as_float("42.5") == 42.5
