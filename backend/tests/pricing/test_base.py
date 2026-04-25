"""Unit tests for the pricing provider abstraction.

Exercises :class:`PriceQuote` defaults, :class:`BasePricingProvider`
contract enforcement, and the :data:`PROVIDERS` registry. The base
classes are pure Python — no DB, no HTTP — so the tests use light fakes
in place of an :class:`Event`.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.pricing import registry
from backend.pricing.base import BasePricingProvider, PriceQuote


class _FakeEvent:
    """Minimal stand-in for :class:`backend.data.models.events.Event`.

    Providers only ever read attributes off the event in production, so
    a simple object with the right field names is enough to exercise
    the abstract contract.

    Attributes:
        external_id: Mirrors the column the providers branch on first.
        source_platform: Used by providers to short-circuit when the
            event isn't carried on this surface.
    """

    def __init__(self, external_id: str = "evt-1", source_platform: str = "test"):
        """Initialize the fake event.

        Args:
            external_id: Identifier the provider would forward upstream.
            source_platform: Platform string the orchestrator filters
                on when fanning out.
        """
        self.external_id = external_id
        self.source_platform = source_platform


class _NoopProvider(BasePricingProvider):
    """Concrete provider that always returns ``None``.

    Used to verify that subclasses can satisfy the ABC and that the
    orchestrator's ``None`` skip path is well-defined.
    """

    name = "noop"

    def fetch(self, event):  # type: ignore[override]
        """Return no quote for any event.

        Args:
            event: Ignored; this provider abstains by contract.

        Returns:
            ``None`` — no price opinion for any input.
        """
        return None


class _StaticProvider(BasePricingProvider):
    """Concrete provider that returns a fixed quote.

    Useful for asserting that the contract supports providers that
    always have something to say (e.g., a primary-source provider with
    face-value data).
    """

    name = "static"

    def fetch(self, event):  # type: ignore[override]
        """Return a constant quote regardless of the event.

        Args:
            event: Ignored; the provider's quote is constant.

        Returns:
            A :class:`PriceQuote` with min/max/buy URL filled in.
        """
        return PriceQuote(
            source=self.name,
            min_price=10.0,
            max_price=50.0,
            buy_url="https://example.test/event",
        )


def test_price_quote_defaults_match_documented_contract() -> None:
    """``PriceQuote`` should default optional fields to ``None`` / empties.

    The orchestrator persists ``raw_data`` straight to the snapshot
    JSONB, and the UI distinguishes "no data" from "explicit zero" — so
    the defaults are part of the public contract, not just convenience.
    """
    quote = PriceQuote(source="seatgeek")
    assert quote.source == "seatgeek"
    assert quote.min_price is None
    assert quote.max_price is None
    assert quote.average_price is None
    assert quote.listing_count is None
    assert quote.currency == "USD"
    assert quote.buy_url is None
    assert quote.affiliate_url is None
    assert quote.is_active is True
    assert quote.raw == {}


def test_price_quote_is_frozen() -> None:
    """``PriceQuote`` is frozen so callers can hash and cache lists.

    The orchestrator may want to dedupe identical quotes in the future;
    freezing the dataclass makes that safe to assume.
    """
    quote = PriceQuote(source="seatgeek")
    with pytest.raises(FrozenInstanceError):
        quote.min_price = 5.0  # type: ignore[misc]


def test_price_quote_raw_default_is_independent_per_instance() -> None:
    """Default ``raw`` must not be a shared mutable across instances.

    Using ``field(default_factory=dict)`` keeps every quote with its
    own dict; this test guards against a regression to a class-level
    default that would entangle providers' raw payloads.
    """
    a = PriceQuote(source="x")
    b = PriceQuote(source="y")
    assert a.raw is not b.raw


def test_base_pricing_provider_cannot_be_instantiated_directly() -> None:
    """The abstract base must reject direct instantiation.

    If ``BasePricingProvider`` ever lost ``@abstractmethod`` on
    ``fetch``, the orchestrator could silently iterate a no-op base
    instance. The ABC machinery prevents that.
    """
    with pytest.raises(TypeError):
        BasePricingProvider()  # type: ignore[abstract]


def test_concrete_provider_returning_none_is_supported() -> None:
    """A provider may legitimately return ``None`` to abstain.

    The orchestrator treats ``None`` as "this surface doesn't carry
    the event" and skips it without raising.
    """
    provider = _NoopProvider()
    assert provider.fetch(_FakeEvent()) is None
    assert provider.name == "noop"


def test_concrete_provider_returns_price_quote() -> None:
    """A provider with data returns a populated :class:`PriceQuote`.

    Sanity-checks the round trip from subclass implementation through
    the abstract contract — proves the type signature is honoured.
    """
    provider = _StaticProvider()
    quote = provider.fetch(_FakeEvent())
    assert quote is not None
    assert quote.source == "static"
    assert quote.min_price == 10.0
    assert quote.max_price == 50.0
    assert quote.buy_url == "https://example.test/event"


def test_registry_returns_snapshot_not_internal_list() -> None:
    """``get_providers`` returns a copy, not a live view.

    Mutating the returned list must not poison the cached registry
    state — the orchestrator and tests rely on that immutability so
    one consumer can't silently change the provider set for another.
    """
    try:
        registry.set_providers_for_testing([_StaticProvider()])
        snapshot = registry.get_providers()
        assert len(snapshot) == 1
        snapshot.append(_NoopProvider())
        assert len(registry.get_providers()) == 1
    finally:
        registry.reset_providers()


def test_set_providers_for_testing_replaces_cache() -> None:
    """``set_providers_for_testing`` swaps the registry contents.

    Lets fixtures install stub providers without touching the real
    SeatGeek/Ticketmaster constructors. ``reset_providers`` then
    restores the default-building behaviour for subsequent tests.
    """
    try:
        registry.set_providers_for_testing([])
        assert registry.get_providers() == []
        registry.set_providers_for_testing([_NoopProvider(), _StaticProvider()])
        names = [p.name for p in registry.get_providers()]
        assert names == ["noop", "static"]
    finally:
        registry.reset_providers()
