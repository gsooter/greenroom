"""Tests for the custom The Camel scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.the_camel import THE_CAMEL_URL, TheCamelScraper

_FIXTURE = """
<html><body>
  <div class="uui-layout88_list w-dyn-items">

    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <a class="link-block-2 w-inline-block"
         href="/shows/blues-and-brews-the-moogly-blues-band-24-apr">
        <div class="show-image-wrapper">
          <img class="image-40" src="https://cdn.example/moogly.jpeg" />
          <div class="date-sticker-multi-day w-condition-invisible">
            <div class="event-month-multi">Apr 24</div>
            <div class="event-month">-</div>
            <div class="event-month-multi">Apr 24</div>
          </div>
          <div class="date-sticker">
            <div class="event-month">Apr</div>
            <div class="event-day">24</div>
            <div class="event-time-new">5:00 pm</div>
          </div>
        </div>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Blues and Brews: The Moogly Blues Band</h3>
        </div>
      </a>
      <img class="event-tag w-condition-invisible"
           src="https://cdn.example/Sold%20out.webp" />
    </div>

    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <a class="link-block-2 w-inline-block" href="/shows/justin-golden-25-apr">
        <div class="show-image-wrapper">
          <img class="image-40 w-condition-invisible"
               src="https://cdn.example/hidden.jpg" />
          <img class="image-40" src="https://cdn.example/justin.jpeg" />
          <div class="date-sticker">
            <div class="event-month">Apr</div>
            <div class="event-day">25</div>
            <div class="event-time-new">8:00 pm</div>
          </div>
        </div>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Justin Golden Matinee</h3>
        </div>
      </a>
      <img alt="Sold out"
           class="event-tag"
           src="https://cdn.example/Sold%20out.webp" />
    </div>

    <div class="uui-layout88_item w-dyn-item" role="listitem">
      <!-- broken: no parseable date -->
      <a href="/shows/no-date">
        <div class="show-image-wrapper">
          <div class="date-sticker">
            <div class="event-month">???</div>
            <div class="event-day"></div>
          </div>
        </div>
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">No Date Show</h3>
        </div>
      </a>
    </div>

  </div>
</body></html>
"""


@responses.activate
def test_parses_show_blocks_with_year_inferred() -> None:
    """Two parseable cards yield RawEvents; the broken third is dropped."""
    responses.add(responses.GET, THE_CAMEL_URL, body=_FIXTURE, status=200)

    events = list(TheCamelScraper(today=date(2026, 4, 25)).scrape())

    assert len(events) == 2
    e1, e2 = events
    assert e1.title == "Blues and Brews: The Moogly Blues Band"
    assert e1.venue_external_id == "the-camel"
    # 2026-04-24 is one day before reference: still in current year
    assert e1.starts_at.isoformat() == "2026-04-24T17:00:00"
    assert e1.ticket_url == (
        "https://www.thecamel.org/shows/blues-and-brews-the-moogly-blues-band-24-apr"
    )
    assert e1.image_url == "https://cdn.example/moogly.jpeg"
    assert e1.raw_data["status"] is None

    assert e2.title == "Justin Golden Matinee"
    assert e2.starts_at.isoformat() == "2026-04-25T20:00:00"
    assert e2.image_url == "https://cdn.example/justin.jpeg"
    assert e2.raw_data["status"] == "sold out"


@responses.activate
def test_skips_block_with_unparseable_date() -> None:
    """Cards with garbage month/day text are dropped, not raised."""
    responses.add(responses.GET, THE_CAMEL_URL, body=_FIXTURE, status=200)

    titles = [e.title for e in TheCamelScraper(today=date(2026, 4, 1)).scrape()]
    assert "No Date Show" not in titles


@responses.activate
def test_default_time_when_clock_missing() -> None:
    """Missing ``.event-time-new`` falls back to 8 PM."""
    html = """
    <html><body>
      <div class="uui-layout88_item w-dyn-item">
        <a href="/shows/timeless">
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
    responses.add(responses.GET, THE_CAMEL_URL, body=html, status=200)

    event = next(TheCamelScraper(today=date(2026, 4, 25)).scrape())
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0
