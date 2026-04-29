"""Pricing-provider abstraction.

Every ticketing surface — Tier A APIs (SeatGeek, Ticketmaster, TickPick),
Tier B page scrapers (Etix, AXS, DICE, Eventbrite, See Tickets), and
future Tier C partner integrations — implements
:class:`BasePricingProvider`. The orchestrator iterates the registry and
treats every provider identically; adding a new one never requires
changes to the engine, the route layer, or the daily sweep task.

Providers are pure data access: they fetch, parse, and return
:class:`PriceQuote` instances. They do not write to the database. The
service layer is responsible for converting quotes into snapshots and
upserting pricing-link rows so the persistence story stays in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.data.models.events import Event


@dataclass(frozen=True)
class PriceQuote:
    """A single (provider, event) pricing observation.

    Returned by :meth:`BasePricingProvider.fetch`. Frozen so the service
    layer can hash a list of quotes for caching without copying.

    Attributes:
        source: Provider identifier; matches the ``source`` column on
            :class:`~backend.data.models.events.TicketPricingSnapshot`
            and :class:`~backend.data.models.events.EventPricingLink`
            (e.g., ``"seatgeek"``, ``"ticketmaster"``, ``"tickpick"``).
        min_price: Lowest available ticket price in ``currency``.
            ``None`` when the provider couldn't quote a low end (rare —
            usually means a face-value-only response).
        max_price: Highest available ticket price.
        average_price: Average of currently listed prices when the
            provider exposes one. ``None`` for primary-source providers
            that only return face-value ranges.
        listing_count: Number of active listings the provider currently
            shows. ``None`` when the provider doesn't expose a count;
            ``0`` is meaningful and means "URL still resolves but no
            inventory right now".
        currency: ISO 4217 currency code; defaults to USD since every
            DMV listing today is USD-quoted.
        buy_url: Canonical buy URL for this event on this provider.
            ``None`` only if the provider has price data but won't
            disclose a deep-link (uncommon).
        affiliate_url: Affiliate-tagged version of ``buy_url`` when the
            provider has a partner program; the UI prefers this over
            ``buy_url`` when both are present.
        is_active: ``True`` when the provider found live inventory on
            this fetch; ``False`` when the URL still resolves but is
            sold out / off-sale. Drives the ``is_active`` flag on the
            corresponding :class:`EventPricingLink`.
        raw: Full upstream payload, persisted to
            :attr:`TicketPricingSnapshot.raw_data` so the future ML
            layer has every field the provider returned without us
            having to predict which ones matter.
    """

    source: str
    min_price: float | None = None
    max_price: float | None = None
    average_price: float | None = None
    listing_count: int | None = None
    currency: str = "USD"
    buy_url: str | None = None
    affiliate_url: str | None = None
    is_active: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


class BasePricingProvider(ABC):
    """Contract every pricing surface must satisfy.

    A provider is stateless and side-effect-free: ``fetch`` is a pure
    function of its constructor-bound configuration plus the event
    argument. Persistence happens in :mod:`backend.services.tickets`,
    not here, so a provider can be unit-tested with a fake event and an
    HTTP fixture without ever touching the database.

    Subclasses must define :attr:`name` as a stable string — it is the
    foreign key into both ``ticket_pricing_snapshots.source`` and
    ``event_pricing_links.source``, so renaming it is a migration.

    Attributes:
        name: Stable provider identifier persisted to the DB.
    """

    name: str

    @abstractmethod
    def fetch(self, event: Event) -> PriceQuote | None:
        """Look up current pricing for one event.

        Args:
            event: The event to price. Providers consult fields like
                ``external_id``, ``source_platform``, ``ticket_url``,
                ``title``, ``starts_at``, and ``venue`` — whatever the
                upstream API or page needs to disambiguate.

        Returns:
            A :class:`PriceQuote` when the provider successfully looked
            the event up. ``None`` when this provider has no opinion
            (the event isn't carried on this surface, the upstream
            returned 404, etc.) — the orchestrator skips ``None``
            silently. Transient errors (timeout, 5xx) should raise so
            the orchestrator can record the failure and back off.
        """
        ...
