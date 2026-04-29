"""Tests for the scraper-origin (Tier B) pricing provider.

The provider is pure logic — no HTTP, no DB — so tests use light fakes
in place of the ORM :class:`Event` and instantiate the provider with
different ``name`` values to exercise the matching contract.
"""

from __future__ import annotations

from backend.pricing.providers.scraper_origin import ScraperOriginPricingProvider


class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        source_platform: Origin scraper identifier; the provider only
            quotes events whose value matches its configured ``name``.
        ticket_url: Buy URL captured by the scraper.
        min_price: Lowest price captured by the scraper.
        max_price: Highest price captured by the scraper.
    """

    def __init__(
        self,
        *,
        source_platform: str | None = None,
        ticket_url: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ):
        """Initialize the fake event.

        Args:
            source_platform: Origin scraper identifier.
            ticket_url: Optional buy URL.
            min_price: Optional minimum captured price.
            max_price: Optional maximum captured price.
        """
        self.source_platform = source_platform
        self.ticket_url = ticket_url
        self.min_price = min_price
        self.max_price = max_price


def test_provider_quotes_only_events_from_its_platform() -> None:
    """Each instance only opines on events whose origin matches its name.

    Without this guard a single registered Tier B provider would emit
    a quote for every event in the catalog, double-counting the
    snapshot with the wrong source string.
    """
    provider = ScraperOriginPricingProvider(name="dice")

    own_event = _FakeEvent(
        source_platform="dice",
        ticket_url="https://dice.fm/event/123",
        min_price=20.0,
        max_price=40.0,
    )
    foreign_event = _FakeEvent(
        source_platform="ticketmaster",
        ticket_url="https://ticketmaster.com/x",
        min_price=50.0,
    )

    own_quote = provider.fetch(own_event)
    assert own_quote is not None
    assert own_quote.source == "dice"
    assert own_quote.buy_url == "https://dice.fm/event/123"
    assert own_quote.min_price == 20.0
    assert own_quote.max_price == 40.0

    assert provider.fetch(foreign_event) is None


def test_provider_abstains_when_no_url_and_no_prices() -> None:
    """A bare event from this platform but with no captured fields abstains.

    Persisting that as a snapshot would carry no signal — UI has
    nothing to render and the future ML layer has no input.
    """
    provider = ScraperOriginPricingProvider(name="black_cat")
    event = _FakeEvent(source_platform="black_cat")
    assert provider.fetch(event) is None


def test_provider_quotes_url_only_when_prices_missing() -> None:
    """A URL with no captured prices still produces a usable quote.

    Many small-venue scrapers don't extract prices reliably; we still
    want the buy URL persisted so the UI can offer the link and the
    user can compare against Tier A providers manually.
    """
    provider = ScraperOriginPricingProvider(name="pie_shop")
    event = _FakeEvent(
        source_platform="pie_shop",
        ticket_url="https://pieshopdc.com/event/x",
    )
    quote = provider.fetch(event)
    assert quote is not None
    assert quote.buy_url == "https://pieshopdc.com/event/x"
    assert quote.min_price is None
    assert quote.max_price is None
    assert quote.is_active is True


def test_provider_quotes_prices_only_when_url_missing() -> None:
    """Prices without a URL still produce a quote.

    Some scrapers capture pricing from a schema.org block but not the
    final buy link; the snapshot history is still valuable as ML
    training data even without a clickable URL on the link row.
    """
    provider = ScraperOriginPricingProvider(name="comet_ping_pong")
    event = _FakeEvent(
        source_platform="comet_ping_pong",
        min_price=15.0,
        max_price=25.0,
    )
    quote = provider.fetch(event)
    assert quote is not None
    assert quote.buy_url is None
    assert quote.min_price == 15.0
    assert quote.max_price == 25.0
    assert quote.is_active is True


def test_provider_handles_event_without_source_platform() -> None:
    """An event whose origin platform is ``None`` produces no quote.

    Defensive: legacy rows could lack the column. Without a match the
    provider simply abstains rather than emitting a stray quote.
    """
    provider = ScraperOriginPricingProvider(name="dice")
    event = _FakeEvent(source_platform=None, ticket_url="https://x.test")
    assert provider.fetch(event) is None


def test_provider_passes_through_zero_prices() -> None:
    """A captured ``min_price = 0`` is preserved, not coerced to ``None``.

    Free events are real (record-store in-stores, festival fringe);
    persisting ``0.0`` is meaningful to the future ML layer and should
    not be flattened.
    """
    provider = ScraperOriginPricingProvider(name="dice")
    event = _FakeEvent(
        source_platform="dice",
        ticket_url="https://dice.fm/x",
        min_price=0.0,
        max_price=0.0,
    )
    quote = provider.fetch(event)
    assert quote is not None
    assert quote.min_price == 0.0
    assert quote.max_price == 0.0


def test_two_provider_instances_have_independent_names() -> None:
    """The registry uses one class per platform — names must not leak.

    Sanity check that nothing class-level was held mutable; each
    instance owns its own ``name``.
    """
    a = ScraperOriginPricingProvider(name="dice")
    b = ScraperOriginPricingProvider(name="black_cat")
    assert a.name == "dice"
    assert b.name == "black_cat"


def test_default_provider_set_includes_tier_b_origins() -> None:
    """The default registry exposes every Tier B origin as a provider.

    Pins the contract that adding a scraper origin to
    ``TIER_B_SCRAPER_ORIGINS`` makes it a registered source. The
    orchestrator iterates the registry, so a missing entry would mean
    those events get quoted only by Tier A providers, dropping the
    venue's own scraper data.
    """
    from backend.pricing import registry

    try:
        registry.reset_providers()
        names = [p.name for p in registry.get_providers()]
        for origin in registry.TIER_B_SCRAPER_ORIGINS:
            assert origin in names
        assert "seatgeek" in names
        assert "ticketmaster" in names
        assert "tickpick" in names
    finally:
        registry.reset_providers()
