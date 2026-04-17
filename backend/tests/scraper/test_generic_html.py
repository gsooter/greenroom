"""Tests for GenericHtmlScraper — the JSON-LD-first HTML platform scraper."""

from __future__ import annotations

import responses

from backend.scraper.platforms.generic_html import GenericHtmlScraper

_PAGE_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{
  "@type": "MusicEvent",
  "name": "Fake Show",
  "startDate": "2026-05-15T20:00:00-04:00",
  "url": "https://venue.example.com/events/1",
  "performer": {"@type": "MusicGroup", "name": "Fake Band"},
  "offers": {"@type": "Offer", "price": 30, "url": "/buy/1"}
}
</script>
</head><body>irrelevant body</body></html>
"""

_PAGE_WITHOUT_JSONLD = """
<html><head><title>Nothing here</title></head>
<body><h1>No structured data</h1></body></html>
"""


@responses.activate
def test_yields_events_from_jsonld() -> None:
    """The scraper yields a RawEvent for every JSON-LD Event on the page."""
    url = "https://venue.example.com/events"
    responses.add(responses.GET, url, body=_PAGE_WITH_JSONLD, status=200)

    scraper = GenericHtmlScraper(url=url)
    events = list(scraper.scrape())

    assert len(events) == 1
    event = events[0]
    assert event.title == "Fake Show"
    assert event.venue_external_id == url
    assert event.source_url == "https://venue.example.com/events/1"
    assert event.artists == ["Fake Band"]
    assert event.min_price == 30.0
    assert event.ticket_url == "https://venue.example.com/buy/1"


@responses.activate
def test_yields_nothing_when_page_has_no_jsonld() -> None:
    """Without JSON-LD the scraper silently returns no events."""
    url = "https://venue.example.com/empty"
    responses.add(responses.GET, url, body=_PAGE_WITHOUT_JSONLD, status=200)

    events = list(GenericHtmlScraper(url=url).scrape())
    assert events == []


@responses.activate
def test_custom_venue_external_id_is_respected() -> None:
    """An explicit venue_external_id overrides the default URL-based key."""
    url = "https://venue.example.com/events"
    responses.add(responses.GET, url, body=_PAGE_WITH_JSONLD, status=200)

    scraper = GenericHtmlScraper(url=url, venue_external_id="custom-venue")
    events = list(scraper.scrape())
    assert events[0].venue_external_id == "custom-venue"
