"""Tests for the custom Pearl Street Warehouse scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.pearl_street_warehouse import (
    PEARL_STREET_URL,
    PearlStreetWarehouseScraper,
)

_FIXTURE = """
<html><body>
  <div class="show-list w-dyn-items">

    <div class="show-item w-dyn-item" role="listitem">
      <a class="show-card-link w-inline-block"
         href="/shows/sam-greenfield-25-apr">
        <div class="show-image-wrapper">
          <img class="image-40"
               src="https://cdn.example/sam.jpg" />
        </div>
        <div class="date-tag date-tag-small">
          <div class="event-day-day">Sat</div>
          <div class="event-month">Apr</div>
          <div class="event-day">25</div>
        </div>
        <div class="event-grid-presenter">All Good Presents</div>
        <h3 class="uui-heading-xxsmall-2 show-card-header">Sam Greenfield</h3>
        <div class="uui-text-size-medium dark-caps">Everyday Everybody</div>
        <div class="show-info">
          <div class="base-text-size-caps">Pearl Street Warehouse</div>
          <div class="base-text-size-caps">All Ages</div>
        </div>
        <div class="show-info">
          <div class="base-text-size-caps text-gap">DOORS</div>
          <div class="base-text-size-caps">7:00 pm</div>
          <div class="base-text-size-caps line-gap">|</div>
          <div class="base-text-size-caps text-gap">Show</div>
          <div class="base-text-size-caps">8:00 pm</div>
        </div>
      </a>
      <div class="event-tag sold-out w-condition-invisible"></div>
      <div class="event-tag last-call w-condition-invisible"></div>
    </div>

    <div class="show-item w-dyn-item" role="listitem">
      <a class="show-card-link" href="/shows/flatliners-3-may">
        <div class="show-image-wrapper">
          <img class="image-40" src="https://cdn.example/flat.jpg" />
        </div>
        <div class="date-tag date-tag-small">
          <div class="event-day-day">Sun</div>
          <div class="event-month">May</div>
          <div class="event-day">3</div>
        </div>
        <div class="event-grid-presenter w-dyn-bind-empty"></div>
        <h3 class="show-card-header">The Flatliners</h3>
        <div class="show-info">
          <div class="base-text-size-caps text-gap">DOORS</div>
          <div class="base-text-size-caps">7:00 pm</div>
          <div class="base-text-size-caps line-gap">|</div>
          <div class="base-text-size-caps text-gap">Show</div>
          <div class="base-text-size-caps">7:30 pm</div>
        </div>
      </a>
      <div class="event-tag sold-out w-condition-invisible"></div>
      <div class="event-tag last-call"></div>
    </div>

    <div class="show-item w-dyn-item" role="listitem">
      <!-- intentionally missing date — should be skipped -->
      <h3 class="show-card-header">Mystery Date</h3>
      <div class="date-tag">
        <div class="event-month">???</div>
        <div class="event-day"></div>
      </div>
    </div>

  </div>
</body></html>
"""


@responses.activate
def test_parses_psw_homepage_into_raw_events() -> None:
    """Two well-formed cards yield RawEvents; the broken third is dropped."""
    responses.add(responses.GET, PEARL_STREET_URL, body=_FIXTURE, status=200)

    scraper = PearlStreetWarehouseScraper(today=date(2026, 4, 25))
    events = list(scraper.scrape())

    assert len(events) == 2

    e1, e2 = events
    assert e1.title == "Sam Greenfield"
    assert e1.venue_external_id == "pearl-street-warehouse"
    assert e1.starts_at.isoformat() == "2026-04-25T20:00:00"
    assert e1.ticket_url == (
        "https://pearlstreetwarehouse.com/shows/sam-greenfield-25-apr"
    )
    assert e1.image_url == "https://cdn.example/sam.jpg"
    assert "Everyday Everybody" in e1.artists
    assert e1.raw_data["presenter"] == "All Good Presents"
    assert e1.raw_data["status"] is None

    assert e2.title == "The Flatliners"
    # 7:30 pm show time → 19:30
    assert e2.starts_at.isoformat() == "2026-05-03T19:30:00"
    assert e2.raw_data["presenter"] is None
    assert e2.raw_data["status"] == "last call"


@responses.activate
def test_default_time_when_show_label_missing() -> None:
    """Cards without a Show-labelled time fall back to 8 PM."""
    html = """
    <html><body>
      <div class="show-item w-dyn-item">
        <a class="show-card-link" href="/shows/no-time">
          <div class="show-image-wrapper">
            <img class="image-40" src="https://cdn.example/x.jpg" />
          </div>
          <div class="date-tag">
            <div class="event-month">Jun</div>
            <div class="event-day">1</div>
          </div>
          <h3 class="show-card-header">Timeless Band</h3>
        </a>
      </div>
    </body></html>
    """
    responses.add(responses.GET, PEARL_STREET_URL, body=html, status=200)

    event = next(PearlStreetWarehouseScraper(today=date(2026, 4, 25)).scrape())
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0


@responses.activate
def test_status_class_dasherized_to_label() -> None:
    """Multi-word status classes ("last-call") become spaced labels."""
    html = """
    <html><body>
      <div class="show-item w-dyn-item">
        <a class="show-card-link" href="/shows/last-call-show">
          <div class="show-image-wrapper">
            <img class="image-40" src="https://cdn.example/x.jpg" />
          </div>
          <div class="date-tag">
            <div class="event-month">Jun</div>
            <div class="event-day">7</div>
          </div>
          <h3 class="show-card-header">Almost Sold Out</h3>
        </a>
        <div class="event-tag last-call"></div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, PEARL_STREET_URL, body=html, status=200)

    event = next(PearlStreetWarehouseScraper(today=date(2026, 4, 25)).scrape())
    assert event.raw_data["status"] == "last call"


@responses.activate
def test_skips_card_with_unparseable_date() -> None:
    """Cards with garbage month or day text are dropped, not raised."""
    responses.add(responses.GET, PEARL_STREET_URL, body=_FIXTURE, status=200)

    titles = [
        e.title for e in PearlStreetWarehouseScraper(today=date(2026, 4, 25)).scrape()
    ]
    assert "Mystery Date" not in titles
