"""Tests for the TickPick search-link pricing provider.

The provider makes no network calls — it builds a deterministic
search URL from event metadata. Tests use light fakes in place of the
ORM :class:`Event`.
"""

from __future__ import annotations

from typing import Any

from backend.pricing.providers.tickpick import (
    TICKPICK_SEARCH_BASE,
    TickPickPricingProvider,
)


class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        title: Event title; provider falls back to it when no artists
            are present.
        artists: Performer list; first entry becomes the search query.
    """

    _UNSET: Any = object()

    def __init__(
        self,
        *,
        title: str | None = "Some Show",
        artists: Any = _UNSET,
    ):
        """Initialize the fake event.

        Args:
            title: Optional event title.
            artists: Performer list. Pass ``[]`` to test the no-artists
                fallback explicitly; omitting supplies a default.
        """
        self.title = title
        if artists is _FakeEvent._UNSET:
            self.artists = ["Headline Artist"]
        else:
            self.artists = artists


def test_fetch_uses_headline_artist_for_search_url() -> None:
    """The first non-empty artist becomes the ``q`` parameter.

    TickPick search ranks artist matches above title matches, so the
    headliner is the better seed for landing on a real event page.
    """
    quote = TickPickPricingProvider().fetch(
        _FakeEvent(title="Some Tour", artists=["Phoebe Bridgers"])
    )
    assert quote is not None
    assert quote.source == "tickpick"
    assert quote.buy_url == f"{TICKPICK_SEARCH_BASE}?q=Phoebe+Bridgers"
    assert quote.min_price is None
    assert quote.max_price is None
    assert quote.is_active is True


def test_fetch_falls_back_to_title_when_artists_missing() -> None:
    """With no artists, the title becomes the query.

    Title-based search is less precise but still useful — better a
    fuzzy match than no link at all for events scraped without clean
    performer metadata.
    """
    quote = TickPickPricingProvider().fetch(
        _FakeEvent(title="Mystery Hits Live", artists=[])
    )
    assert quote is not None
    assert quote.buy_url == f"{TICKPICK_SEARCH_BASE}?q=Mystery+Hits+Live"


def test_fetch_returns_none_without_artists_or_title() -> None:
    """Provider abstains when there's nothing to search on.

    A quote with no prices and no buy URL would just be noise — the
    orchestrator can't render a link or persist a useful snapshot.
    """
    quote = TickPickPricingProvider().fetch(_FakeEvent(title="", artists=[]))
    assert quote is None


def test_fetch_skips_blank_artists_in_search() -> None:
    """Whitespace-only artist entries are skipped before falling back.

    Some scrapers emit lists like ``["", "Real Artist"]`` for events
    where the support act wasn't announced; we should treat the
    blanks as absent rather than searching for an empty string.
    """
    quote = TickPickPricingProvider().fetch(
        _FakeEvent(artists=["", "  ", "Real Artist"])
    )
    assert quote is not None
    assert quote.buy_url == f"{TICKPICK_SEARCH_BASE}?q=Real+Artist"
