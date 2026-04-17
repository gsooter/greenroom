"""Tests for the custom Pie Shop scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.pie_shop import PIE_SHOP_URL, PieShopScraper

_FIXTURE = """
<html><body>
  <div class="uui-layout88_list w-dyn-items">
    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <a class="link-block-2 w-inline-block" href="/shows/band-a-17-apr">
        <div class="show-image-wrapper">
          <img class="image-40 w-condition-invisible"
               src="https://cdn.example/hidden.jpg" />
          <img class="image-40" src="https://cdn.example/band-a.jpg"
               alt="Band A" />
          <div class="date-sticker">
            <div class="event-month">Apr</div>
            <div class="event-day">17</div>
            <div class="event-time-new">8:00 pm</div>
          </div>
        </div>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Band A</h3>
        </div>
      </a>
      <img alt="Event status indicator"
           class="event-tag"
           src="https://cdn.example/Sold%20out.webp" />
    </div>
    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <a class="link-block-2" href="/shows/band-b-19-may">
        <div class="show-image-wrapper">
          <img class="image-40" src="https://cdn.example/band-b.jpg" />
          <div class="date-sticker">
            <div class="event-month">May</div>
            <div class="event-day">19</div>
            <div class="event-time-new">7:30 pm</div>
          </div>
        </div>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Band B w/ Opener</h3>
        </div>
      </a>
      <img alt="Event status indicator"
           class="event-tag w-condition-invisible"
           src="https://cdn.example/placeholder.webp" />
    </div>
    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <!-- intentionally missing date — should be skipped -->
      <div class="show-image-wrapper">
        <div class="date-sticker">
          <div class="event-month">???</div>
          <div class="event-day"></div>
        </div>
      </div>
      <h3 class="uui-heading-xxsmall-2">No Date Show</h3>
    </div>
  </div>
</body></html>
"""


@responses.activate
def test_parses_upcoming_events_with_rolled_year() -> None:
    """Events in months that have already passed roll into next year."""
    responses.add(responses.GET, PIE_SHOP_URL, body=_FIXTURE, status=200)

    # Reference date is late in April so "Apr 17" is in the past
    # and should roll into 2027; "May 19" is still ahead in 2026.
    scraper = PieShopScraper(today=date(2026, 4, 25))
    events = list(scraper.scrape())

    assert len(events) == 2

    e1, e2 = events
    assert e1.title == "Band A"
    assert e1.starts_at.isoformat() == "2027-04-17T20:00:00"
    assert e1.venue_external_id == "pie-shop"
    assert e1.ticket_url == "https://www.pieshopdc.com/shows/band-a-17-apr"
    assert e1.image_url == "https://cdn.example/band-a.jpg"
    assert e1.raw_data["status"] == "sold out"

    assert e2.title == "Band B w/ Opener"
    assert e2.starts_at.isoformat() == "2026-05-19T19:30:00"
    assert e2.raw_data["status"] is None


@responses.activate
def test_skips_blocks_with_unparseable_dates() -> None:
    """Blocks missing month/day or with bad values are dropped, not raised."""
    responses.add(responses.GET, PIE_SHOP_URL, body=_FIXTURE, status=200)

    events = list(PieShopScraper(today=date(2026, 4, 1)).scrape())
    titles = [e.title for e in events]
    assert "No Date Show" not in titles


@responses.activate
def test_default_time_when_clock_missing() -> None:
    """When ``.event-time-new`` is absent the scraper falls back to 8pm."""
    html = """
    <html><body>
      <div class="uui-layout88_item w-dyn-item">
        <a href="/shows/undated-time">
          <div class="show-image-wrapper">
            <img class="image-40" src="https://cdn.example/x.jpg" />
            <div class="date-sticker">
              <div class="event-month">Jun</div>
              <div class="event-day">3</div>
            </div>
          </div>
          <h3 class="uui-heading-xxsmall-2">Timeless Band</h3>
        </a>
      </div>
    </body></html>
    """
    responses.add(responses.GET, PIE_SHOP_URL, body=html, status=200)

    event = next(PieShopScraper(today=date(2026, 4, 1)).scrape())
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0
