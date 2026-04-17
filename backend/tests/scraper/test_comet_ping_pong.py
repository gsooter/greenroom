"""Tests for the Comet Ping Pong Firebooking-iframe scraper."""

from __future__ import annotations

import responses

from backend.scraper.venues.comet_ping_pong import (
    COMET_CALENDAR_URL,
    CometPingPongScraper,
)

_FIXTURE = """
<html><body>
  <div class="uui-layout88_list w-dyn-items">
    <div class="uui-layout88_item-cpp w-dyn-item">
      <div class="div-block-10">
        <a class="link-block-4 w-inline-block" href="/shows/forager-17-apr">
          <img class="image-42" src="https://cdn.example/forager.jpg" />
        </a>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">FORAGER, Berra</h3>
          <div class="div-block-12">
            <h2 class="heading-date">April 17, 2026</h2>
            <h2 class="heading-time">10:00 pm</h2>
          </div>
          <div class="ages-2">All Ages</div>
        </div>
        <div class="uui-button-row-3">
          <a class="link-block-2" href="/shows/forager-17-apr">Buy Now</a>
        </div>
      </div>
      <img alt="Sold Out" class="event-tag"
           src="https://cdn.example/soldout.png" />
    </div>
    <div class="uui-layout88_item-cpp w-dyn-item">
      <div class="div-block-10">
        <a class="link-block-4" href="/shows/left-lane-cruiser-18-apr">
          <img class="image-42" src="https://cdn.example/llc.jpg" />
        </a>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Left Lane Cruiser, Roscoe Tripp</h3>
          <div class="div-block-12">
            <h2 class="heading-date">April 18, 2026</h2>
            <h2 class="heading-time">10:00 pm</h2>
          </div>
          <div class="ages-2">21+</div>
        </div>
        <div class="uui-button-row-3">
          <a class="link-block-2" href="/shows/left-lane-cruiser-18-apr">Buy Now</a>
        </div>
      </div>
      <img alt="Sold Out" class="event-tag w-condition-invisible"
           src="https://cdn.example/soldout.png" />
    </div>
    <div class="uui-layout88_item-cpp w-dyn-item">
      <!-- Invalid date, should be skipped -->
      <h3 class="uui-heading-xxsmall-2">Bad Date</h3>
      <h2 class="heading-date">Someday</h2>
    </div>
  </div>
</body></html>
"""


@responses.activate
def test_parses_firebooking_iframe_events() -> None:
    """Every valid ``.uui-layout88_item-cpp`` block yields a RawEvent."""
    responses.add(responses.GET, COMET_CALENDAR_URL, body=_FIXTURE, status=200)

    events = list(CometPingPongScraper().scrape())
    assert len(events) == 2

    first = events[0]
    assert first.title == "FORAGER, Berra"
    assert first.venue_external_id == "comet-ping-pong"
    assert first.starts_at.isoformat() == "2026-04-17T22:00:00"
    assert first.artists == ["FORAGER", "Berra"]
    assert first.ticket_url == (
        "https://calendar.rediscoverfirebooking.com/shows/forager-17-apr"
    )
    assert first.image_url == "https://cdn.example/forager.jpg"
    assert first.raw_data["ages"] == "All Ages"
    assert first.raw_data["status"] == "sold out"

    second = events[1]
    assert second.raw_data["status"] is None
    assert second.raw_data["ages"] == "21+"


@responses.activate
def test_splits_comma_and_and_joined_titles_into_artists() -> None:
    """Comma- or ``and``-separated show titles produce multiple artists."""
    html = """
    <html><body>
      <div class="uui-layout88_item-cpp w-dyn-item">
        <h3 class="uui-heading-xxsmall-2">Band A and Band B, Band C</h3>
        <div class="div-block-12">
          <h2 class="heading-date">May 3, 2026</h2>
          <h2 class="heading-time">8:00 pm</h2>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, COMET_CALENDAR_URL, body=html, status=200)

    event = next(CometPingPongScraper().scrape())
    assert event.artists == ["Band A", "Band B", "Band C"]
