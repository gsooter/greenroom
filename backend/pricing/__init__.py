"""Multi-source ticket pricing pipeline.

A registry of :class:`~backend.pricing.base.BasePricingProvider`
implementations, each owning a single ticketing surface (SeatGeek,
Ticketmaster, TickPick, scraped primary venues, etc.). The orchestrator
in :mod:`backend.services.tickets` fans out to every active provider on
each refresh, persists the resulting :class:`PriceQuote` rows as
:class:`~backend.data.models.events.TicketPricingSnapshot` history, and
upserts the corresponding :class:`~backend.data.models.events.EventPricingLink`
buy-URL records.

Adding a new provider is one file under ``providers/`` plus one entry
in :data:`backend.pricing.registry.PROVIDERS`. No other module changes
are required — the engine, the manual refresh endpoint, and the daily
sweep all iterate the registry.
"""
