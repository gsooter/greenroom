"""Tests for the custom The Hamilton Live scraper."""

from __future__ import annotations

import json

import responses

from backend.scraper.venues.the_hamilton import (
    HAMILTON_REST_URL,
    TheHamiltonScraper,
)

_DETAIL_TEMPLATE = """
<html><body>
  <div class="entry-content">
    <div class="single-view" id="seetickets">
      <div class="container">
        <div class="single-view-item see-event-id-{wp_id}">
          <div class="list-img">
            <span class="dates">{date_text}</span>
            <a href="https://wl.seetickets.us/event/{slug}/{ext_id}?afflky=Hamilton"
               class="image-url">
              <img src="https://live.thehamiltondc.com/wp-content/uploads/poster.jpg"
                   alt="{title}">
            </a>
          </div>
          <div class="list-right">
            <div class="single-view-details">
              <div class="artist-info">
                <h1>
                  <a href="https://wl.seetickets.us/event/{slug}/{ext_id}">
                    {title}
                  </a>
                </h1>
              </div>
              <div class="artist-details">
                <div class="detail detail_event_date">
                  <div class="label">Event Date</div>
                  <div class="name">{event_date}</div>
                </div>
                <div class="detail detail_event_time">
                  <div class="label">Event Time</div>
                  <div class="name">{event_time}</div>
                </div>
                <div class="detail detail_price_range">
                  <div class="label">Min Ticket Price</div>
                  <div class="name">{price_range}</div>
                </div>
                <div class="detail detail_ticket_status">
                  <div class="label">Status</div>
                  <div class="name">{status}</div>
                </div>
              </div>
              <div class="ticket-price">
                <a class="event_button get-tickets"
                   href="https://wl.seetickets.us/event/{slug}/{ext_id}?afflky=Hamilton">
                  Get Tickets
                </a>
              </div>
            </div>
          </div>
        </div>
        <div class="event-description">{description}</div>
      </div>
    </div>
  </div>
</body></html>
"""


def _detail_html(**kwargs: object) -> str:
    """Render the See Tickets detail HTML with the given fields."""
    defaults = {
        "wp_id": 6154,
        "slug": "john-scofields-electrospective",
        "ext_id": "685362",
        "title": "John Scofield&#8217;s Electrospective",
        "date_text": "10/22/2026",
        "event_date": "Thu Oct 22",
        "event_time": "8:00 pm",
        "price_range": "$75.00-$115.00",
        "status": "gettickets",
        "description": "An evening with John Scofield.",
    }
    defaults.update(kwargs)
    return _DETAIL_TEMPLATE.format(**defaults)


def _rest_record(
    *,
    wp_id: int,
    slug: str,
    title: str,
    link: str,
) -> dict[str, object]:
    """Build a minimal WP REST event record matching the live API shape."""
    return {
        "id": wp_id,
        "slug": slug,
        "title": {"rendered": title},
        "link": link,
        "type": "event",
    }


def _rest_url(page: int, per_page: int = 100) -> str:
    """Construct the WP REST URL the scraper will hit for a given page."""
    return f"{HAMILTON_REST_URL}?per_page={per_page}&page={page}"


@responses.activate
def test_parses_event_with_full_detail_page() -> None:
    """A WP REST record + detail page resolve into a complete RawEvent."""
    detail_url = "https://live.thehamiltondc.com/event/john-scofields-electrospective/"
    responses.add(
        responses.GET,
        _rest_url(1),
        json=[
            _rest_record(
                wp_id=6154,
                slug="john-scofields-electrospective",
                title="John Scofield&#8217;s Electrospective",
                link=detail_url,
            ),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(responses.GET, detail_url, body=_detail_html(), status=200)

    events = list(TheHamiltonScraper().scrape())

    assert len(events) == 1
    event = events[0]
    assert event.title == "John Scofield\u2019s Electrospective"
    assert event.venue_external_id == "the-hamilton"
    assert event.starts_at.isoformat() == "2026-10-22T20:00:00"
    assert event.source_url == detail_url
    assert event.ticket_url == (
        "https://wl.seetickets.us/event/"
        "john-scofields-electrospective/685362?afflky=Hamilton"
    )
    assert event.image_url == (
        "https://live.thehamiltondc.com/wp-content/uploads/poster.jpg"
    )
    assert event.min_price == 75.0
    assert event.max_price == 115.0
    assert event.description == "An evening with John Scofield."
    assert event.raw_data["wp_id"] == 6154
    assert event.raw_data["status"] == "gettickets"
    assert event.raw_data["source"] == "the_hamilton_seetickets_v2"


@responses.activate
def test_pagination_stops_when_page_size_under_per_page() -> None:
    """Scraper stops paginating once a page returns fewer than per_page rows."""
    detail_url_a = "https://live.thehamiltondc.com/event/band-a/"
    detail_url_b = "https://live.thehamiltondc.com/event/band-b/"

    responses.add(
        responses.GET,
        _rest_url(1, per_page=2),
        json=[
            _rest_record(wp_id=1, slug="band-a", title="Band A", link=detail_url_a),
            _rest_record(wp_id=2, slug="band-b", title="Band B", link=detail_url_b),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        _rest_url(2, per_page=2),
        json=[],  # empty short page → stop
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        detail_url_a,
        body=_detail_html(
            wp_id=1, slug="band-a", title="Band A", date_text="06/01/2026"
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        detail_url_b,
        body=_detail_html(
            wp_id=2, slug="band-b", title="Band B", date_text="07/02/2026"
        ),
        status=200,
    )

    scraper = TheHamiltonScraper(per_page=2, max_pages=5)
    events = list(scraper.scrape())

    assert {e.title for e in events} == {"Band A", "Band B"}


@responses.activate
def test_skips_event_when_detail_page_lacks_seetickets_block() -> None:
    """Events whose detail page is missing the See Tickets block are dropped."""
    detail_url = "https://live.thehamiltondc.com/event/private-event/"
    responses.add(
        responses.GET,
        _rest_url(1),
        json=[
            _rest_record(
                wp_id=99,
                slug="private-event",
                title="Private Event",
                link=detail_url,
            ),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        detail_url,
        body="<html><body><p>No event block here.</p></body></html>",
        status=200,
    )

    events = list(TheHamiltonScraper().scrape())

    assert events == []


@responses.activate
def test_falls_back_to_8pm_when_event_time_missing() -> None:
    """Missing ``detail_event_time`` defaults the show time to 8 PM."""
    detail_url = "https://live.thehamiltondc.com/event/timeless/"
    responses.add(
        responses.GET,
        _rest_url(1),
        json=[
            _rest_record(wp_id=300, slug="timeless", title="Timeless", link=detail_url),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        detail_url,
        body=_detail_html(wp_id=300, slug="timeless", title="Timeless", event_time=""),
        status=200,
    )

    event = next(TheHamiltonScraper().scrape())
    assert event.starts_at.hour == 20
    assert event.starts_at.minute == 0


@responses.activate
def test_handles_single_price_value() -> None:
    """A ``$45.00`` price string sets min and max to the same value."""
    detail_url = "https://live.thehamiltondc.com/event/single-price/"
    responses.add(
        responses.GET,
        _rest_url(1),
        json=[
            _rest_record(
                wp_id=400, slug="single-price", title="Single Price", link=detail_url
            ),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        detail_url,
        body=_detail_html(
            wp_id=400, slug="single-price", title="Single Price", price_range="$45.00"
        ),
        status=200,
    )

    event = next(TheHamiltonScraper().scrape())
    assert event.min_price == 45.0
    assert event.max_price == 45.0


@responses.activate
def test_unparseable_date_drops_event() -> None:
    """Detail pages with no parseable MM/DD/YYYY are skipped."""
    detail_url = "https://live.thehamiltondc.com/event/bad-date/"
    responses.add(
        responses.GET,
        _rest_url(1),
        json=[
            _rest_record(wp_id=500, slug="bad-date", title="Bad Date", link=detail_url),
        ],
        status=200,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        detail_url,
        body=_detail_html(
            wp_id=500, slug="bad-date", title="Bad Date", date_text="TBA"
        ),
        status=200,
    )

    events = list(TheHamiltonScraper().scrape())
    assert events == []


@responses.activate
def test_non_json_rest_response_yields_zero_events() -> None:
    """REST endpoint returning HTML or other garbage stops cleanly with 0 events."""
    responses.add(
        responses.GET,
        _rest_url(1),
        body="<html>error</html>",
        status=200,
        match_querystring=True,
    )

    events = list(TheHamiltonScraper().scrape())
    assert events == []


@responses.activate
def test_record_without_link_is_skipped() -> None:
    """Records missing the ``link`` field never trigger detail fetches."""
    record = _rest_record(wp_id=600, slug="no-link", title="No Link", link="")
    record["link"] = None  # explicit
    responses.add(
        responses.GET,
        _rest_url(1),
        body=json.dumps([record]),
        status=200,
        match_querystring=True,
    )

    events = list(TheHamiltonScraper().scrape())
    assert events == []
