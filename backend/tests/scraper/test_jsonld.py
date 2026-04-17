"""Tests for the shared JSON-LD event extractor."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.scraper.base.jsonld import extract_events


def _build_html(scripts: list[str]) -> str:
    """Wrap a list of script bodies in a minimal HTML document.

    Args:
        scripts: List of raw JSON strings to embed in ``application/ld+json`` blocks.

    Returns:
        A minimal HTML document containing one script tag per entry.
    """
    blocks = "\n".join(
        f'<script type="application/ld+json">{body}</script>' for body in scripts
    )
    return f"<html><head>{blocks}</head><body></body></html>"


def test_extracts_basic_music_event() -> None:
    """A single MusicEvent node yields a fully populated RawEvent."""
    html = _build_html(
        [
            """
            {
              "@context": "https://schema.org",
              "@type": "MusicEvent",
              "name": "Wilco Live",
              "url": "https://venue.example.com/events/wilco",
              "startDate": "2026-05-10T20:00:00-04:00",
              "endDate": "2026-05-10T23:00:00-04:00",
              "image": "https://venue.example.com/img/wilco.jpg",
              "description": "An evening with Wilco.",
              "performer": [
                {"@type": "MusicGroup", "name": "Wilco"},
                {"@type": "MusicGroup", "name": "Opener"}
              ],
              "offers": {
                "@type": "Offer",
                "url": "/tickets/wilco",
                "price": 45.00,
                "priceCurrency": "USD",
                "validFrom": "2026-03-01T10:00:00-05:00"
              }
            }
            """
        ]
    )

    events = list(
        extract_events(
            html,
            source_url="https://venue.example.com/events",
            venue_external_id="venue-1",
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.title == "Wilco Live"
    assert event.venue_external_id == "venue-1"
    assert event.starts_at.isoformat() == "2026-05-10T20:00:00-04:00"
    assert event.ends_at is not None
    assert event.source_url == "https://venue.example.com/events/wilco"
    assert event.image_url == "https://venue.example.com/img/wilco.jpg"
    assert event.description == "An evening with Wilco."
    assert event.artists == ["Wilco", "Opener"]
    assert event.ticket_url == "https://venue.example.com/tickets/wilco"
    assert event.min_price == 45.0
    assert event.max_price == 45.0
    assert event.on_sale_at is not None


def test_flattens_graph_and_filters_non_events() -> None:
    """@graph payloads are flattened; non-Event nodes are ignored."""
    html = _build_html(
        [
            """
            {
              "@context": "https://schema.org",
              "@graph": [
                {"@type": "WebSite", "url": "https://venue.example.com/"},
                {"@type": "Event", "name": "Show 1", "startDate": "2026-06-01"},
                {"@type": ["Thing", "MusicEvent"], "name": "Show 2",
                 "startDate": "2026-07-01T21:00:00Z"}
              ]
            }
            """
        ]
    )

    events = list(
        extract_events(
            html,
            source_url="https://venue.example.com/",
            venue_external_id="venue-2",
        )
    )

    titles = [e.title for e in events]
    assert titles == ["Show 1", "Show 2"]
    assert events[1].starts_at == datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)


def test_malformed_block_does_not_crash_extractor() -> None:
    """One bad block must not prevent valid blocks from yielding events."""
    html = _build_html(
        [
            "{this is not valid json",
            """
            {"@type": "MusicEvent", "name": "Good", "startDate": "2026-08-01"}
            """,
        ]
    )

    events = list(
        extract_events(
            html,
            source_url="https://venue.example.com/",
            venue_external_id="venue-3",
        )
    )
    assert len(events) == 1
    assert events[0].title == "Good"


def test_missing_required_fields_are_skipped() -> None:
    """Nodes without a title or parseable start date are dropped."""
    html = _build_html(
        [
            """
            [
              {"@type": "Event", "startDate": "2026-09-01"},
              {"@type": "Event", "name": "No Date"},
              {"@type": "Event", "name": "Ok", "startDate": "not-a-date"},
              {"@type": "Event", "name": "Kept", "startDate": "2026-09-02"}
            ]
            """
        ]
    )

    events = list(
        extract_events(
            html,
            source_url="https://venue.example.com/",
            venue_external_id="venue-4",
        )
    )
    assert [e.title for e in events] == ["Kept"]


def test_offer_list_picks_min_and_max() -> None:
    """A list of offers should produce min/max across their prices."""
    html = _build_html(
        [
            """
            {
              "@type": "Event",
              "name": "Tiered Pricing",
              "startDate": "2026-10-01T19:00:00",
              "offers": [
                {"@type": "Offer", "price": "20"},
                {"@type": "Offer", "lowPrice": 15, "highPrice": 55}
              ]
            }
            """
        ]
    )

    event = next(
        extract_events(
            html,
            source_url="https://venue.example.com/",
            venue_external_id="venue-5",
        )
    )
    assert event.min_price == 15.0
    assert event.max_price == 55.0
