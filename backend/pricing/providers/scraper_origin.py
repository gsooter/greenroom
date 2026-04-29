"""Tier B scraper-origin pricing provider.

Promotes the pricing fields the existing scrapers already capture
into the multi-source pipeline. When the daily venue sweep re-runs
the scraper for a venue, it overwrites ``events.min_price``,
``events.max_price``, and ``events.ticket_url``; this provider then
turns those columns into a :class:`PriceQuote` so DICE, Etix, AXS,
Eventbrite, See Tickets, and the venue-specific scrapers all become
named sources alongside SeatGeek and Ticketmaster.

A single class, instantiated once per platform name in the registry,
gives every scraper a distinct ``source`` value for the snapshot and
pricing-link tables without duplicating code. The price freshness for
Tier B sources is whatever cadence the underlying scraper runs at —
the orchestrator does not call back into the scraper here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.pricing.base import BasePricingProvider, PriceQuote

if TYPE_CHECKING:
    from backend.data.models.events import Event


class ScraperOriginPricingProvider(BasePricingProvider):
    """Quote prices from the event's own scraper-captured fields.

    The provider only activates when the event's ``source_platform``
    matches the configured platform — every event has exactly one
    origin platform, so at most one Tier B provider quotes any given
    event. SeatGeek and Ticketmaster handle their own platforms via
    Tier A providers; if a Tier B provider is also registered for
    those names it would still work, but the Tier A entry returns
    fresher data with richer fields, so the registry keeps the Tier A
    provider as the single representative.

    Attributes:
        name: Persisted source value; set to the underlying platform
            (e.g., ``"dice"``, ``"etix"``, ``"black_cat"``).
    """

    def __init__(self, name: str) -> None:
        """Initialize the provider for one origin platform.

        Args:
            name: Platform identifier matching the
                :attr:`backend.scraper.base.scraper.BaseScraper.source_platform`
                value the scraper writes to ``events.source_platform``.
        """
        self.name = name

    def fetch(self, event: Event) -> PriceQuote | None:
        """Return a quote built from the event's existing fields.

        Args:
            event: The event to price. The provider abstains unless
                ``event.source_platform`` matches its configured name
                — every Tier B provider quotes only its own origin
                events to avoid double-counting.

        Returns:
            A :class:`PriceQuote` carrying the scraper's last-captured
            min/max/ticket URL when the event came from this platform
            and has at least a URL or a price, ``None`` otherwise.
        """
        if getattr(event, "source_platform", None) != self.name:
            return None

        ticket_url = getattr(event, "ticket_url", None)
        min_price = getattr(event, "min_price", None)
        max_price = getattr(event, "max_price", None)

        if not ticket_url and min_price is None and max_price is None:
            return None

        is_active = ticket_url is not None or (
            min_price is not None or max_price is not None
        )

        return PriceQuote(
            source=self.name,
            min_price=min_price,
            max_price=max_price,
            average_price=None,
            listing_count=None,
            currency="USD",
            buy_url=ticket_url,
            affiliate_url=None,
            is_active=is_active,
            raw={},
        )
