"""RawEvent dataclass and related scraper data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawEvent:
    """Raw event data as scraped from a source before normalization.

    Attributes:
        title: Event title or headline act name.
        venue_external_id: External identifier for the venue.
        starts_at: Event start datetime.
        source_url: URL of the original event listing.
        raw_data: Full original payload from the source.
        artists: List of artist/performer names.
        description: Event description text, if available.
        ticket_url: Direct ticket purchase URL, if available.
        min_price: Minimum ticket price in USD, if available.
        max_price: Maximum ticket price in USD, if available.
        image_url: Event or artist image URL, if available.
        ends_at: Event end datetime, if available.
        on_sale_at: Ticket on-sale datetime, if available.
        genres: Canonical genre tags extracted at scrape time (e.g. from
            Ticketmaster classifications). Empty when the source does
            not surface genre metadata — per-artist Spotify enrichment
            fills the gap in that case.
    """

    title: str
    venue_external_id: str
    starts_at: datetime
    source_url: str
    raw_data: dict[str, Any]
    artists: list[str] = field(default_factory=list)
    description: str | None = None
    ticket_url: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    image_url: str | None = None
    ends_at: datetime | None = None
    on_sale_at: datetime | None = None
    genres: list[str] = field(default_factory=list)
