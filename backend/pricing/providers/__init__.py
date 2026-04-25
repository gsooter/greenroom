"""Concrete pricing providers, one module per ticketing surface.

Each provider is a thin :class:`~backend.pricing.base.BasePricingProvider`
subclass that knows how to talk to one upstream and return a
:class:`PriceQuote`. Modules here are deliberately small — request,
parse, return — with no coupling to the orchestrator, the database,
or each other.
"""
