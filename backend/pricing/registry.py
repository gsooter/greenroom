"""Active pricing-provider registry.

A flat list of :class:`~backend.pricing.base.BasePricingProvider`
instances the orchestrator should consult on every refresh. Splitting
the registry from the providers themselves keeps a single, grep-able
inventory of what surfaces are live, and lets tests substitute their
own list without monkey-patching the providers.

Adding a new provider is two lines: import its class, append an
instance. Removing one is deleting one line. No engine code touches
this file, so the orchestrator never has to know which providers exist
at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.pricing.base import BasePricingProvider

PROVIDERS: list[BasePricingProvider] = []
"""The active provider set, evaluated in declaration order.

Empty at the start of the multi-source pricing rollout — providers are
appended here as each one ships (SeatGeek, Ticketmaster, TickPick, then
the Tier B scraper-derived sources). The orchestrator iterates this
list and is stable against an empty registry, so partial rollouts and
test environments are both safe.
"""


def get_providers() -> list[BasePricingProvider]:
    """Return the currently registered pricing providers.

    Wrapping the module-level list in an accessor keeps test setup
    consistent — fixtures can monkey-patch this function instead of
    mutating the global list, which avoids leaking provider sets
    between test cases.

    Returns:
        A snapshot list of the active providers. Callers may iterate
        but should not mutate; mutation should go through this module.
    """
    return list(PROVIDERS)
