"""TickPick search-link pricing provider.

TickPick has no public price API — the partner program requires a
signed agreement we don't have today — so this provider can't quote
prices the way SeatGeek and Ticketmaster do. What it *can* do is
build a deterministic search URL that lands the user on the right
event page on tickpick.com, which is still a useful third destination
to compare against the Tier A providers.

The quote it returns has no prices, no listing count, and no
``raw`` payload (we never make a request). It carries only a
``buy_url`` and ``is_active=True``. The orchestrator persists it as
an :class:`EventPricingLink` so the UI can offer "Compare on
TickPick" alongside the live-priced options.

When the partner API becomes available, this module is the natural
home for a follow-up that hits ``api.tickpick.com`` and fills in the
missing fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from backend.pricing.base import BasePricingProvider, PriceQuote

if TYPE_CHECKING:
    from backend.data.models.events import Event


TICKPICK_SEARCH_BASE = "https://www.tickpick.com/search"


class TickPickPricingProvider(BasePricingProvider):
    """Provider that hands off to TickPick search.

    Produces a buy URL only; live prices are out of reach without
    partner credentials. Returning a quote with no prices is
    deliberately part of the contract — the orchestrator filters on
    ``buy_url`` when persisting :class:`EventPricingLink` rows, so a
    URL-only quote still drives a real user-visible link.

    Attributes:
        name: Persisted as ``ticket_pricing_snapshots.source`` and
            ``event_pricing_links.source``.
    """

    name = "tickpick"

    def fetch(self, event: Event) -> PriceQuote | None:
        """Build a TickPick search URL for one event.

        Args:
            event: The event to link to.

        Returns:
            A :class:`PriceQuote` carrying only a buy URL when we have
            enough event metadata to build a meaningful search;
            ``None`` when the event has neither artists nor a title.
        """
        query = self._search_query(event)
        if not query:
            return None

        url = f"{TICKPICK_SEARCH_BASE}?q={quote_plus(query)}"
        return PriceQuote(
            source=self.name,
            buy_url=url,
            is_active=True,
        )

    def _search_query(self, event: Event) -> str | None:
        """Pick the most distinctive search token for the event.

        Prefers the headliner artist over the title — TickPick's search
        ranks artist matches much higher than free-text title matches,
        so an artist query is more likely to land on a real event page.

        Args:
            event: The event we're linking to.

        Returns:
            A non-empty query string, or ``None`` when the event has
            no artists and no title.
        """
        artists = getattr(event, "artists", None) or []
        if artists:
            headline = next((a for a in artists if a and a.strip()), None)
            if headline:
                return headline.strip()
        title = getattr(event, "title", None)
        if title and title.strip():
            return title.strip()
        return None
