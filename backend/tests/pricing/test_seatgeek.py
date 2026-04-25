"""Tests for :class:`backend.pricing.providers.seatgeek.SeatGeekPricingProvider`.

The provider is a thin HTTP client over SeatGeek's Platform API. Tests
mock the HTTP layer with ``responses`` so nothing touches the network,
and use light dataclass-style fakes in place of the SQLAlchemy
:class:`Event` model — the provider only ever reads attributes off the
event, so duck typing is enough.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import requests
import responses

from backend.pricing.providers.seatgeek import (
    SEATGEEK_BASE_URL,
    SeatGeekPricingProvider,
    _as_float,
)


class _FakeVenue:
    """Stand-in for :class:`backend.data.models.venues.Venue`.

    Attributes:
        external_ids: JSONB column the provider reads to find a
            SeatGeek venue id mapping.
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

    Carries just the attributes the provider reads — title, artists,
    starts_at, source_platform, external_id, and venue.

    Attributes:
        title: Event headline used as a search-query fallback.
        artists: Performer list; the first entry becomes the search
            query when present.
        starts_at: Event start datetime; drives the date-window filter.
        source_platform: Set to ``"seatgeek"`` to exercise the direct
            ID path.
        external_id: SeatGeek event id when ``source_platform`` is
            ``"seatgeek"``; ignored otherwise.
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
                fallback explicitly; omitting the argument supplies a
                default headliner.
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


def _make_provider(
    *, client_id: str = "test-id", client_secret: str | None = "test-secret"
) -> SeatGeekPricingProvider:
    """Construct a provider with explicit credentials for tests.

    Args:
        client_id: SeatGeek client id to send.
        client_secret: SeatGeek client secret; pass ``None`` to test
            the no-secret read-only path.

    Returns:
        A configured :class:`SeatGeekPricingProvider`.
    """
    return SeatGeekPricingProvider(client_id=client_id, client_secret=client_secret)


def _events_payload(stats: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Build a SeatGeek-shaped event payload for fixtures.

    Args:
        stats: Stats subdocument (lowest_price, highest_price, etc.).
        **overrides: Top-level fields to set on the returned event.

    Returns:
        A dict shaped like a single SeatGeek event response.
    """
    payload = {
        "id": "sg-event-1",
        "url": "https://seatgeek.com/event-1",
        "stats": stats,
    }
    payload.update(overrides)
    return payload


# -------------------------------------------------------------- direct ID path


@responses.activate  # type: ignore[misc]
def test_fetch_uses_direct_id_when_event_sourced_from_seatgeek() -> None:
    """A SeatGeek-origin event hits ``/events/{id}`` and skips search.

    Events that were scraped from SeatGeek already carry the canonical
    id, so the provider should bypass search entirely.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events/sg-123",
        json=_events_payload(
            {
                "lowest_price": 25,
                "highest_price": 200,
                "average_price": 80,
                "listing_count": 12,
            }
        ),
        status=200,
    )
    event = _FakeEvent(source_platform="seatgeek", external_id="sg-123")

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert quote.source == "seatgeek"
    assert quote.min_price == 25.0
    assert quote.max_price == 200.0
    assert quote.average_price == 80.0
    assert quote.listing_count == 12
    assert quote.buy_url == "https://seatgeek.com/event-1"
    assert quote.is_active is True


@responses.activate  # type: ignore[misc]
def test_direct_id_404_falls_back_to_search() -> None:
    """A 404 on direct lookup retries via search rather than returning ``None``.

    Events sometimes get re-listed under a new SeatGeek id; the search
    fallback is the recovery path that lets a stale id still resolve.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events/old-id",
        json={},
        status=404,
    )
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={
            "events": [
                _events_payload(
                    {"lowest_price": 50, "highest_price": 100, "listing_count": 3}
                )
            ]
        },
        status=200,
    )
    event = _FakeEvent(source_platform="seatgeek", external_id="old-id")

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert quote.min_price == 50.0


# ---------------------------------------------------------------- search path


@responses.activate  # type: ignore[misc]
def test_search_passes_venue_query_and_date_window() -> None:
    """The search call includes venue.id, q, and a ±24h datetime window.

    Without the venue and date filters SeatGeek returns far too many
    matches for a generic artist name; the test pins the contract so
    the orchestrator gets a focused result set.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={
            "events": [
                _events_payload(
                    {"lowest_price": 30, "highest_price": 120, "listing_count": 5}
                )
            ]
        },
        status=200,
    )
    venue = _FakeVenue(external_ids={"seatgeek": "venue-99"})
    event = _FakeEvent(
        title="Some Tour",
        artists=["Phoebe Bridgers"],
        starts_at=datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        venue=venue,
    )

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert len(responses.calls) == 1
    sent = responses.calls[0].request.url
    assert "venue.id=venue-99" in sent
    assert "q=Phoebe+Bridgers" in sent
    # Date window is ±24h; SeatGeek expects a naive isoformat.
    assert "datetime_utc.gte=2026-04-30T20%3A00%3A00" in sent
    assert "datetime_utc.lte=2026-05-02T20%3A00%3A00" in sent
    assert "client_id=test-id" in sent
    assert "client_secret=test-secret" in sent


@responses.activate  # type: ignore[misc]
def test_search_falls_back_to_title_when_no_artists() -> None:
    """With no artist list, the search ``q`` falls back to the event title.

    A handful of scrapers don't capture artist names cleanly (festival
    bills, tribute nights). Title is still a useful search seed.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={"events": [_events_payload({"lowest_price": 10})]},
        status=200,
    )
    event = _FakeEvent(title="Mystery Hits Live", artists=[])

    quote = _make_provider().fetch(event)

    assert quote is not None
    assert "q=Mystery+Hits+Live" in responses.calls[0].request.url


@responses.activate  # type: ignore[misc]
def test_search_returning_no_events_yields_none() -> None:
    """An empty ``events`` array makes the provider abstain.

    SeatGeek doesn't carry every show in the database; abstaining is
    correct so the orchestrator skips silently and the link table
    isn't bloated with "no inventory" rows.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={"events": []},
        status=200,
    )
    quote = _make_provider().fetch(_FakeEvent())
    assert quote is None


@responses.activate  # type: ignore[misc]
def test_search_omits_secret_when_unset() -> None:
    """An empty ``client_secret`` is left off the query string entirely.

    SeatGeek's read endpoints accept a client_id alone; sending an
    empty secret would break the auth handshake on stricter proxies.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={"events": [_events_payload({"lowest_price": 5})]},
        status=200,
    )
    provider = _make_provider(client_secret="")
    provider.fetch(_FakeEvent())
    sent = responses.calls[0].request.url
    assert "client_id=test-id" in sent
    assert "client_secret" not in sent


# -------------------------------------------------------------- payload parsing


@responses.activate  # type: ignore[misc]
def test_zero_listings_marks_quote_inactive_but_keeps_url() -> None:
    """``listing_count == 0`` flips ``is_active`` off without dropping the row.

    Decoupling pricing-link from snapshot relies on this: a sold-out
    state preserves the buy URL on the link row so the next refresh
    that sees inventory just bumps the activity timestamps.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events/sg-1",
        json=_events_payload(
            {"lowest_price": None, "highest_price": None, "listing_count": 0}
        ),
        status=200,
    )
    event = _FakeEvent(source_platform="seatgeek", external_id="sg-1")
    quote = _make_provider().fetch(event)
    assert quote is not None
    assert quote.is_active is False
    assert quote.listing_count == 0
    assert quote.buy_url == "https://seatgeek.com/event-1"


@responses.activate  # type: ignore[misc]
def test_payload_without_url_or_prices_returns_none() -> None:
    """A degenerate payload (no URL, no prices) abstains.

    Persisting a row with no buy URL and no price range would just be
    noise — the UI has nothing to render and the snapshot has no
    signal for the future ML layer.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events/sg-empty",
        json={"id": "sg-empty", "stats": {}},
        status=200,
    )
    event = _FakeEvent(source_platform="seatgeek", external_id="sg-empty")
    assert _make_provider().fetch(event) is None


@responses.activate  # type: ignore[misc]
def test_non_json_response_returns_none_without_raising() -> None:
    """A 200 with a non-JSON body skips cleanly rather than crashing.

    Upstream proxies sometimes return HTML maintenance pages with a
    200 status; the provider must not crash the orchestrator on those.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        body="<html>maintenance</html>",
        status=200,
    )
    assert _make_provider().fetch(_FakeEvent()) is None


@responses.activate  # type: ignore[misc]
def test_5xx_raises_so_orchestrator_can_back_off() -> None:
    """A 500 from SeatGeek raises so the orchestrator records failure.

    Transient upstream errors must propagate; swallowing them would
    let the daily sweep silently leave events with stale prices.
    """
    responses.add(
        responses.GET,
        f"{SEATGEEK_BASE_URL}/events",
        json={"error": "boom"},
        status=503,
    )
    with pytest.raises(requests.HTTPError):
        _make_provider().fetch(_FakeEvent())


# ----------------------------------------------------------------- _as_float


def test_as_float_handles_none_and_invalid_strings() -> None:
    """The numeric coercion preserves ``None`` and rejects garbage.

    SeatGeek occasionally hands back ``None`` for fields we don't have
    a quote for; coercing those to ``0.0`` would corrupt the historical
    record persisted into the snapshot table.
    """
    assert _as_float(None) is None
    assert _as_float("not-a-number") is None
    assert _as_float(42) == 42.0
    assert _as_float("12.5") == 12.5
