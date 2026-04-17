"""Tests for the Black Cat schedule-page scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.black_cat import BLACK_CAT_URL, BlackCatScraper

_FIXTURE = """
<html><body>
  <div id="main-calendar">
    <div class="show">
      <div class="band-photo-sm">
        <a href="/shows/heavenly.html">
          <img alt="" src="/images/460/heavenly.jpg" />
        </a>
      </div>
      <div class="show-details">
        <h2 class="date">Thursday April 16</h2>
        <h1 class="headline">
          <a href="/shows/heavenly.html">HEAVENLY</a>
        </h1>
        <h2 class="support">LIGHTHEADED</h2>
        <h2 class="support">SWANSEA SOUND</h2>
        <p class="show-text">Doors at 7:30</p>
        <a href="https://www.etix.com/ticket/p/43971320/heavenly">
          <img src="/images/buy-button.gif" alt="buy-button" />
        </a>
      </div>
    </div>
    <div class="show">
      <div class="show-details">
        <h2 class="date">Saturday April 18</h2>
        <h1 class="headline">
          <a href="/shows/depeche-mode-party.html">DEPECHE MODE DANCE PARTY</a>
        </h1>
        <h2 class="support">w/ DJ Steve EP</h2>
        <p class="show-text">Doors at 9:00 PM</p>
      </div>
    </div>
    <div class="show">
      <!-- no date, should be skipped -->
      <div class="show-details">
        <h1 class="headline">Mystery Show</h1>
      </div>
    </div>
  </div>
</body></html>
"""


@responses.activate
def test_parses_schedule_shows_with_doors_time() -> None:
    """Every ``.show`` block becomes a RawEvent with the doors time applied."""
    responses.add(responses.GET, BLACK_CAT_URL, body=_FIXTURE, status=200)

    events = list(BlackCatScraper(today=date(2026, 4, 1)).scrape())
    assert len(events) == 2

    first = events[0]
    assert first.title == "HEAVENLY"
    assert first.venue_external_id == "black-cat"
    assert first.starts_at.isoformat() == "2026-04-16T19:30:00"
    assert first.artists == ["HEAVENLY", "LIGHTHEADED", "SWANSEA SOUND"]
    assert first.description == "Doors at 7:30"
    assert first.ticket_url == "https://www.etix.com/ticket/p/43971320/heavenly"
    assert (
        first.source_url
        == "https://www.blackcatdc.com/shows/heavenly.html"
    )
    assert (
        first.image_url
        == "https://www.blackcatdc.com/images/460/heavenly.jpg"
    )

    second = events[1]
    assert second.title == "DEPECHE MODE DANCE PARTY"
    assert second.starts_at.isoformat() == "2026-04-18T21:00:00"
    assert second.ticket_url is None  # no etix link in this block


@responses.activate
def test_default_doors_fallback_to_8pm() -> None:
    """Shows without a parseable doors line default to 8:00 PM."""
    html = """
    <html><body>
      <div id="main-calendar">
        <div class="show">
          <h2 class="date">Tuesday May 5</h2>
          <h1 class="headline"><a href="/shows/example.html">Example</a></h1>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, BLACK_CAT_URL, body=html, status=200)

    event = next(BlackCatScraper(today=date(2026, 4, 1)).scrape())
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0


@responses.activate
def test_rolls_past_dates_into_next_year() -> None:
    """A date that has already passed this year rolls forward to next year."""
    html = """
    <html><body>
      <div id="main-calendar">
        <div class="show">
          <h2 class="date">Wednesday February 3</h2>
          <h1 class="headline"><a href="/shows/example.html">Winter Show</a></h1>
          <p class="show-text">Doors at 8:00 pm</p>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, BLACK_CAT_URL, body=html, status=200)

    event = next(BlackCatScraper(today=date(2026, 4, 1)).scrape())
    assert event.starts_at.year == 2027
