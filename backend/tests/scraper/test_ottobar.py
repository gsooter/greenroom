"""Tests for the custom Ottobar scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.ottobar import OTTOBAR_URL, OttobarScraper

_FIXTURE = """
<html><body>
  <div class="generalView rhp-desktop-list rhp-mobile-list">

    <span class="rhp-events-list-separator-month d-flex">
      <span>April 2026</span>
    </span>

    <div class="col-12 eventWrapper rhpSingleEvent rhp-event__single-event--list">
      <div class="row g-0">
        <div class="col-12 col-md-3">
          <div class="rhp-event-thumb">
            <a class="url" href="/event/dyke-nite/ottobar/baltimore-maryland/">
              <div class="rhp-events-event-image">
                <img src="https://theottobar.com/wp-content/uploads/dyke-nite.jfif"
                     class="eventListImage rhp-event__image--list" />
              </div>
              <div class="eventDateList rhp-event__date--list">
                <div class="mb-0 eventMonth singleEventDate text-uppercase">
                  Sat, Apr 25
                </div>
              </div>
            </a>
          </div>
        </div>
        <div class="col-12 col-md-9">
          <a id="eventTitle" class="url"
             href="/event/dyke-nite/ottobar/baltimore-maryland/">
            <h2 class="rhp-event__title--list">DYKE NITE</h2>
          </a>
          <div class="eventDateDetails">
            <div class="eventsColor eventDoorStartDate rhp-event__time--list">
              <span class="rhp-event__time-text--list">Doors: 9 pm</span>
            </div>
          </div>
          <span class="rhp-event-cta on-sale">
            <a class="btn btn-primary" target="_blank"
               href="https://www.etix.com/ticket/p/35093049/dyke-nite">
              Buy Tickets
            </a>
          </span>
        </div>
      </div>
    </div>

    <div class="col-12 eventWrapper rhpSingleEvent rhp-event__single-event--list">
      <div class="row g-0">
        <div class="col-12 col-md-3">
          <div class="rhp-event-thumb">
            <a class="url" href="/event/sold-out-show/">
              <img src="https://theottobar.com/wp-content/uploads/sold.jpg"
                   class="eventListImage rhp-event__image--list" />
              <div class="eventDateList rhp-event__date--list">
                <div class="mb-0 eventMonth singleEventDate">Sun, Apr 26</div>
              </div>
            </a>
          </div>
        </div>
        <div class="col-12 col-md-9">
          <a id="eventTitle" class="url" href="/event/sold-out-show/">
            <h2 class="rhp-event__title--list">Solen / Korr / Bound by the Grave</h2>
          </a>
          <span class="rhp-event__time-text--list">Doors: 7 pm</span>
          <span class="rhp-event-cta sold-out">
            <a class="btn btn-primary" href="https://www.etix.com/p/sold">
              Sold Out
            </a>
          </span>
        </div>
      </div>
    </div>

    <span class="rhp-events-list-separator-month d-flex">
      <span>May 2026</span>
    </span>

    <div class="col-12 eventWrapper rhpSingleEvent rhp-event__single-event--list">
      <div class="row g-0">
        <div class="col-12 col-md-9">
          <a id="eventTitle" class="url" href="/event/may-tba/">
            <h2 class="rhp-event__title--list">May TBA Band</h2>
          </a>
          <div class="eventDateList rhp-event__date--list">
            <div class="mb-0 eventMonth singleEventDate">Fri, May 1</div>
          </div>
        </div>
      </div>
    </div>

    <div class="col-12 eventWrapper rhpSingleEvent rhp-event__single-event--list">
      <div class="row g-0">
        <a class="url" href="/event/no-date/">
          <h2 class="rhp-event__title--list">Garbage Date Show</h2>
          <div class="eventDateList rhp-event__date--list">
            <div class="mb-0 eventMonth singleEventDate">Mon, ??? --</div>
          </div>
        </a>
      </div>
    </div>

  </div>
</body></html>
"""


@responses.activate
def test_parses_event_blocks_with_separator_year() -> None:
    """Each event picks up the year from the most recent separator."""
    responses.add(responses.GET, OTTOBAR_URL, body=_FIXTURE, status=200)

    events = list(OttobarScraper(today=date(2026, 4, 25)).scrape())

    assert len(events) == 3
    e1, e2, e3 = events

    assert e1.title == "DYKE NITE"
    assert e1.venue_external_id == "ottobar"
    assert e1.starts_at.isoformat() == "2026-04-25T21:00:00"
    assert e1.ticket_url == ("https://www.etix.com/ticket/p/35093049/dyke-nite")
    assert e1.image_url == ("https://theottobar.com/wp-content/uploads/dyke-nite.jfif")
    assert e1.raw_data["status"] == "on sale"
    assert e1.source_url == (
        "https://theottobar.com/event/dyke-nite/ottobar/baltimore-maryland/"
    )

    assert e2.title == "Solen / Korr / Bound by the Grave"
    assert e2.starts_at.isoformat() == "2026-04-26T19:00:00"
    assert e2.raw_data["status"] == "sold out"

    # The May separator advances the year tracker, so the May event
    # comes back as 2026 even though April has wrapped past it.
    assert e3.title == "May TBA Band"
    assert e3.starts_at.isoformat() == "2026-05-01T20:00:00"
    assert e3.raw_data["status"] is None
    assert e3.ticket_url == "https://theottobar.com/event/may-tba/"


@responses.activate
def test_separator_advances_year_to_next_calendar() -> None:
    """A January separator after a December run rolls events into the new year."""
    fixture = """
    <html><body>
      <div class="generalView">
        <span class="rhp-events-list-separator-month"><span>December 2026</span></span>
        <div class="col-12 eventWrapper rhpSingleEvent">
          <h2 class="rhp-event__title--list">NYE Bash</h2>
          <div class="eventDateList">
            <div class="singleEventDate">Wed, Dec 31</div>
          </div>
          <span class="rhp-event__time-text--list">Doors: 9 pm</span>
        </div>
        <span class="rhp-events-list-separator-month"><span>January 2027</span></span>
        <div class="col-12 eventWrapper rhpSingleEvent">
          <h2 class="rhp-event__title--list">January Band</h2>
          <div class="eventDateList">
            <div class="singleEventDate">Fri, Jan 1</div>
          </div>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, OTTOBAR_URL, body=fixture, status=200)

    events = list(OttobarScraper(today=date(2026, 4, 25)).scrape())

    assert [e.starts_at.isoformat() for e in events] == [
        "2026-12-31T21:00:00",
        "2027-01-01T20:00:00",
    ]


@responses.activate
def test_skips_block_with_unparseable_date() -> None:
    """Cards whose sticker yields no month/day are dropped silently."""
    responses.add(responses.GET, OTTOBAR_URL, body=_FIXTURE, status=200)

    titles = [e.title for e in OttobarScraper(today=date(2026, 4, 1)).scrape()]
    assert "Garbage Date Show" not in titles


@responses.activate
def test_default_time_when_doors_missing() -> None:
    """Missing ``.rhp-event__time-text--list`` falls back to 8 PM."""
    fixture = """
    <html><body>
      <div class="generalView">
        <span class="rhp-events-list-separator-month"><span>June 2026</span></span>
        <div class="col-12 eventWrapper rhpSingleEvent">
          <h2 class="rhp-event__title--list">Timeless</h2>
          <div class="eventDateList">
            <div class="singleEventDate">Wed, Jun 3</div>
          </div>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, OTTOBAR_URL, body=fixture, status=200)

    event = next(OttobarScraper(today=date(2026, 4, 25)).scrape())
    assert event.starts_at.isoformat() == "2026-06-03T20:00:00"


@responses.activate
def test_falls_back_to_year_inference_without_separator() -> None:
    """Events that appear before any separator use ``today`` for year roll."""
    fixture = """
    <html><body>
      <div class="generalView">
        <div class="col-12 eventWrapper rhpSingleEvent">
          <h2 class="rhp-event__title--list">Orphan Show</h2>
          <div class="eventDateList">
            <div class="singleEventDate">Tue, Apr 28</div>
          </div>
          <span class="rhp-event__time-text--list">Doors: 8 pm</span>
        </div>
      </div>
    </body></html>
    """
    responses.add(responses.GET, OTTOBAR_URL, body=fixture, status=200)

    event = next(OttobarScraper(today=date(2026, 4, 25)).scrape())
    assert event.starts_at.isoformat() == "2026-04-28T20:00:00"
