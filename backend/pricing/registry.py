"""Active pricing-provider registry.

The single inventory of which :class:`~backend.pricing.base.BasePricingProvider`
instances the orchestrator consults on every refresh. Centralising the
list here keeps adding a new ticketing surface to a one-line change and
gives tests a single seam to substitute their own provider set without
monkey-patching the providers themselves.

Providers are constructed lazily on first :func:`get_providers` call —
they read API credentials from :mod:`backend.core.config`, so deferring
construction lets unit tests that don't exercise pricing avoid paying
for client setup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.pricing.providers.scraper_origin import ScraperOriginPricingProvider
from backend.pricing.providers.seatgeek import SeatGeekPricingProvider
from backend.pricing.providers.ticketmaster import TicketmasterPricingProvider
from backend.pricing.providers.tickpick import TickPickPricingProvider

TIER_B_SCRAPER_ORIGINS: tuple[str, ...] = (
    "dice",
    "eventbrite",
    "etix",
    "axs",
    "see_tickets",
    "generic_html",
    "black_cat",
    "comet_ping_pong",
    "pie_shop",
    "the_hamilton",
    "the_camel",
    "ottobar",
    "bethesda_theater",
    "pearl_street_warehouse",
)
"""Origin platforms that surface as standalone Tier B sources.

Each entry becomes a registered provider that quotes only events
whose ``source_platform`` matches. The list is the inventory of
scraper names the orchestrator will treat as first-class pricing
surfaces — adding a new venue-specific scraper means appending its
``source_platform`` here so its captured prices are exposed.
"""

if TYPE_CHECKING:
    from backend.pricing.base import BasePricingProvider


_cached_providers: list[BasePricingProvider] | None = None


def _build_default_providers() -> list[BasePricingProvider]:
    """Construct the canonical list of active pricing providers.

    Order matters only for tie-breaking in the UI when two providers
    return the same effective price; SeatGeek leads because it has the
    richest stats payload (lowest, highest, average, listing count)
    and the most reliable inventory.

    Returns:
        Newly constructed provider instances ready to call
        :meth:`fetch`.
    """
    providers: list[BasePricingProvider] = [
        SeatGeekPricingProvider(),
        TicketmasterPricingProvider(),
        TickPickPricingProvider(),
    ]
    providers.extend(
        ScraperOriginPricingProvider(name=origin) for origin in TIER_B_SCRAPER_ORIGINS
    )
    return providers


def get_providers() -> list[BasePricingProvider]:
    """Return the active pricing providers, constructing on first call.

    Memoises the list so the orchestrator doesn't rebuild HTTP clients
    on every refresh. Tests that need to swap provider sets should
    call :func:`set_providers_for_testing` rather than mutate the
    returned list — the snapshot semantics here are intentional.

    Returns:
        A snapshot list of provider instances. Callers may iterate
        but should not mutate; mutation should go through this module.
    """
    global _cached_providers
    if _cached_providers is None:
        _cached_providers = _build_default_providers()
    return list(_cached_providers)


def set_providers_for_testing(providers: list[BasePricingProvider]) -> None:
    """Replace the cached provider set with an explicit list.

    Test-only seam — production code should never call this. Pytest
    fixtures use it to inject stub providers without touching the
    real SeatGeek/Ticketmaster constructors.

    Args:
        providers: The provider set to install. Pass an empty list to
            disable all providers; pass back the result of a prior
            :func:`get_providers` call to restore state.
    """
    global _cached_providers
    _cached_providers = list(providers)


def reset_providers() -> None:
    """Drop the cached provider set so the next call rebuilds defaults.

    Used by tests that mutated the registry via
    :func:`set_providers_for_testing` and want to leave it clean for
    the next case.
    """
    global _cached_providers
    _cached_providers = None
