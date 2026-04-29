"""Tests for the custom Bethesda Theater scraper."""

from __future__ import annotations

from datetime import date

import responses

from backend.scraper.venues.bethesda_theater import (
    BETHESDA_THEATER_URL,
    BethesdaTheaterScraper,
)

# The fixture mirrors how Bethesda's Squarespace homepage actually
# nests rows: an outer page-section row contains many inner rows, one
# per show. The scraper must select the inner rows, not the outer.
_FIXTURE = """
<html><body>
  <div class="row sqs-row">  <!-- outer page section -->

    <div class="row sqs-row">  <!-- show 1 -->
      <div class="col sqs-col-3 span-3">
        <div class="sqs-block-content">
          <h4>SAT APRIL 25| 8:00PM</h4>
          <h4>DOORS/DINNER 6:00PM</h4>
        </div>
      </div>
      <div class="col sqs-col-7 span-7">
        <h3>Blue Notes &amp; The Casuals</h3>
      </div>
      <div class="col sqs-col-2 span-2">
        <img src="https://images.example/blue-notes.gif" />
        <div class="sqs-block-button-container">
          <a class="sqs-block-button" href="https://www.instantseats.com/index.cfm?eventid=A1B2C3D4">
            BUY TICKETS
          </a>
        </div>
      </div>
    </div>

    <div class="row sqs-row">  <!-- show 2 — TicketWeb -->
      <div class="col sqs-col-3 span-3">
        <h4>TUES MAY 5 | 7:00PM</h4>
        <h4>DOORS/DINNER 6:00PM</h4>
      </div>
      <div class="col sqs-col-7 span-7">
        <h3>2026 AMPERS &amp; ONE LIVE TOUR 'Born To Define'</h3>
      </div>
      <div class="col sqs-col-2 span-2">
        <a href="https://www.ticketweb.com/event/2026-ampers-one-bethesda-theater-tickets/14801243">
          BUY TICKETS
        </a>
      </div>
    </div>

    <div class="row sqs-row">  <!-- show 3 — multi-night residency, picks first date -->
      <div class="col sqs-col-3 span-3">
        <h4>FRI MAY 8 | 8:30PM</h4>
        <h4>DOORS/DINNER 6:30PM</h4>
        <h4>SAT MAY 9 | 8:00PM</h4>
        <h4>DOORS/DINNER 6:00PM</h4>
      </div>
      <div class="col sqs-col-7 span-7">
        <h3>STOKLEY</h3>
      </div>
      <div class="col sqs-col-2 span-2">
        <!-- two buttons, both pointing at the same instantseats event -->
        <a href="https://www.instantseats.com/index.cfm?eventid=STOKLEY">BUY TICKETS</a>
        <a href="https://www.instantseats.com/index.cfm?eventid=STOKLEY">BUY TICKETS</a>
      </div>
    </div>

    <div class="row sqs-row">  <!-- show 4 — outer wrapper around an inner sqs-row -->
      <div class="col sqs-col-12">
        <div class="row sqs-row">
          <div class="col sqs-col-3">
            <h4>FRI JUNE 12 | 7:30PM</h4>
          </div>
          <div class="col sqs-col-9">
            <h3>NESTED SHOW</h3>
            <a href="https://www.instantseats.com/index.cfm?eventid=NESTED">
              BUY TICKETS
            </a>
          </div>
        </div>
      </div>
    </div>

    <div class="row sqs-row">  <!-- mailing list — no ticket link -->
      <div class="col sqs-col-12">
        <h3>JOIN OUR MAILING LIST</h3>
      </div>
    </div>

    <div class="row sqs-row">  <!-- show 4 — date with no time -->
      <div class="col sqs-col-3 span-3">
        <h4>SUN DEC 6</h4>
      </div>
      <div class="col sqs-col-9 span-9">
        <h3>KIRK WHALUM</h3>
        <a href="https://www.instantseats.com/index.cfm?eventid=KIRK">BUY TICKETS</a>
      </div>
    </div>

  </div>
</body></html>
"""


@responses.activate
def test_parses_single_ticket_show_rows() -> None:
    """Each row with a single ticket URL becomes a RawEvent."""
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=_FIXTURE, status=200)

    events = list(BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape())

    titles = [e.title for e in events]
    assert "Blue Notes & The Casuals" in titles
    assert "2026 AMPERS & ONE LIVE TOUR 'Born To Define'" in titles
    assert "KIRK WHALUM" in titles
    # multi-night residency: yields one event using the first date.
    assert "STOKLEY" in titles
    # nested wrapper row: the inner row is the narrowest, outer is dropped.
    assert "NESTED SHOW" in titles
    # mailing list block has no ticket link, so it's filtered out.
    assert "JOIN OUR MAILING LIST" not in titles


@responses.activate
def test_outer_wrapper_with_inner_show_picks_inner_only() -> None:
    """When a row nests a sub-row sharing the same ticket, the outer is dropped."""
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=_FIXTURE, status=200)

    events = [
        e
        for e in BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape()
        if e.title == "NESTED SHOW"
    ]
    # Exactly one — the inner row, not double-emitted from the outer wrapper.
    assert len(events) == 1
    assert events[0].starts_at.isoformat() == "2026-06-12T19:30:00"


@responses.activate
def test_multi_night_row_picks_first_parseable_date() -> None:
    """A residency row with multiple ``h4`` dates emits the first one."""
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=_FIXTURE, status=200)

    events = {
        e.title: e for e in BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape()
    }
    assert events["STOKLEY"].starts_at.isoformat() == "2026-05-08T20:30:00"


@responses.activate
def test_parses_date_and_show_time() -> None:
    """The scraper picks up the show time, not the doors/dinner time."""
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=_FIXTURE, status=200)

    events = {
        e.title: e for e in BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape()
    }

    assert events["Blue Notes & The Casuals"].starts_at.isoformat() == (
        "2026-04-25T20:00:00"
    )
    # 7:00PM not 6:00PM
    assert events[
        "2026 AMPERS & ONE LIVE TOUR 'Born To Define'"
    ].starts_at.isoformat() == ("2026-05-05T19:00:00")
    # No time given → defaults to 8 PM
    assert events["KIRK WHALUM"].starts_at.isoformat() == "2026-12-06T20:00:00"


@responses.activate
def test_records_ticket_provider_and_url() -> None:
    """Ticket URL points at the buy link, and provider is identified."""
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=_FIXTURE, status=200)

    events = {
        e.title: e for e in BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape()
    }

    blue = events["Blue Notes & The Casuals"]
    assert blue.ticket_url == (
        "https://www.instantseats.com/index.cfm?eventid=A1B2C3D4"
    )
    assert blue.source_url == blue.ticket_url
    assert blue.image_url == "https://images.example/blue-notes.gif"
    assert blue.raw_data["ticket_provider"] == "instantseats"
    assert blue.venue_external_id == "bethesda-theater"

    ampers = events["2026 AMPERS & ONE LIVE TOUR 'Born To Define'"]
    assert ampers.raw_data["ticket_provider"] == "ticketweb"


@responses.activate
def test_skips_rows_without_a_parseable_date() -> None:
    """A row with a ticket link but no readable ``h4`` date is dropped."""
    html = """
    <html><body>
      <div class="row sqs-row">
        <h3>Mystery Show</h3>
        <a href="https://www.instantseats.com/index.cfm?eventid=MYSTERY">
          BUY TICKETS
        </a>
      </div>
    </body></html>
    """
    responses.add(responses.GET, BETHESDA_THEATER_URL, body=html, status=200)

    events = list(BethesdaTheaterScraper(today=date(2026, 4, 25)).scrape())
    assert events == []
