"""Tests for :class:`backend.scraper.platforms.dice.DiceScraper`.

All HTTP calls are mocked — tests never touch dice.fm. The fixture at
``fixtures/dice_dc9_venue.html`` carries a realistic JSON-LD Place node
with two events (one minimal, one with multiple performers and an
offer) plus a ``__NEXT_DATA__`` payload used exclusively by the
fallback tests. Time-based assertions patch ``time.sleep`` /
``time.monotonic`` so the suite stays fast.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests
import responses

from backend.scraper.platforms import dice as dice_module
from backend.scraper.platforms.dice import DiceScraper, DiceScraperError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dice_dc9_venue.html"
DC9_VENUE_URL = "https://dice.fm/venue/dc9-q2xvo"
ET_OFFSET = timezone(timedelta(hours=-4))


def _load_fixture() -> str:
    """Return the DC9 fixture HTML as a string.

    Returns:
        The fixture file contents decoded as UTF-8 text.
    """
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _make_scraper(
    *, venue_external_id: str = "dc9", url: str = DC9_VENUE_URL
) -> DiceScraper:
    """Construct a DiceScraper for a single-venue test.

    Args:
        venue_external_id: Venue slug to stamp on every RawEvent.
        url: Dice venue URL to scrape.

    Returns:
        A configured :class:`DiceScraper` instance.
    """
    return DiceScraper(venue_external_id=venue_external_id, dice_venue_url=url)


# ---------------------------------------------------------------- JSON-LD path


@responses.activate  # type: ignore[misc]
def test_scrape_returns_events_from_json_ld() -> None:
    """The fixture's Place.event array produces RawEvents."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    events = list(_make_scraper().scrape())
    # Fixture has 2 JSON-LD events and zero malformed ones that matter.
    assert len(events) == 2
    assert {e.title for e in events} == {"Nerd Nite", "Oddisee"}


@responses.activate  # type: ignore[misc]
def test_scrape_correct_field_mapping() -> None:
    """Core fields map from JSON-LD keys to RawEvent slots."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    events = {e.title: e for e in _make_scraper().scrape()}
    nerd = events["Nerd Nite"]

    assert nerd.title == "Nerd Nite"
    # startDate + tz offset survives round-trip as tz-aware datetime.
    assert nerd.starts_at.tzinfo is not None
    assert nerd.starts_at.utcoffset() == timedelta(hours=-4)
    assert nerd.starts_at.year == 2026
    assert nerd.starts_at.month == 4
    assert nerd.starts_at.day == 24
    assert nerd.starts_at.hour == 20

    # Event url is both source_url and ticket_url when offers carry no url.
    expected_url = (
        "https://dice.fm/event/xe6y53-nerd-nite-24th-apr-dc9-washington-tickets"
    )
    assert nerd.source_url == expected_url
    assert nerd.ticket_url == expected_url

    # Image comes from the image list.
    assert nerd.image_url == "https://dice-media.imgix.net/attachments/nerd-nite.jpg"

    # raw_data carries the event URL as the external id for the runner.
    assert nerd.raw_data.get("id") == expected_url


@responses.activate  # type: ignore[misc]
def test_supporting_acts_populated() -> None:
    """Multiple performers → first is headliner, rest are supporting."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    events = {e.title: e for e in _make_scraper().scrape()}
    oddisee = events["Oddisee"]

    assert oddisee.artists[0] == "Oddisee"
    assert oddisee.artists[1:] == ["Heno."]


@responses.activate  # type: ignore[misc]
def test_offer_price_and_ticket_url_mapped() -> None:
    """A single JSON-LD offer populates min_price, max_price, and on_sale_at."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    events = {e.title: e for e in _make_scraper().scrape()}
    oddisee = events["Oddisee"]

    assert oddisee.min_price == 25.0
    assert oddisee.max_price == 25.0
    assert oddisee.on_sale_at is not None
    assert oddisee.on_sale_at.year == 2026
    assert oddisee.on_sale_at.month == 1


@responses.activate  # type: ignore[misc]
def test_starts_at_has_correct_timezone() -> None:
    """Every event's starts_at carries America/New_York's DST offset."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    for event in _make_scraper().scrape():
        assert event.starts_at.tzinfo is not None, event.title
        assert event.starts_at.utcoffset() == timedelta(hours=-4), event.title


@responses.activate  # type: ignore[misc]
def test_raw_data_always_populated() -> None:
    """Every RawEvent carries a non-empty raw_data dict."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    for event in _make_scraper().scrape():
        assert isinstance(event.raw_data, dict)
        assert event.raw_data, event.title


@responses.activate  # type: ignore[misc]
def test_venue_external_id_matches_constructor() -> None:
    """RawEvent.venue_external_id mirrors the constructor arg."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    events = list(_make_scraper(venue_external_id="dc9").scrape())
    assert events
    for event in events:
        assert event.venue_external_id == "dc9"


# ----------------------------------------------------------- fallback path


def _next_data_only_html() -> str:
    """Return HTML that has no JSON-LD but keeps the __NEXT_DATA__ block.

    The fixture deliberately carries both sources so most tests use one
    page. This helper strips JSON-LD blocks for fallback-path tests.

    Returns:
        HTML with every application/ld+json script block removed.
    """
    html = _load_fixture()
    # Drop JSON-LD script tags entirely; keep NEXT_DATA intact.
    import re

    return re.sub(
        r'<script type="application/ld\+json">.*?</script>',
        "",
        html,
        flags=re.DOTALL,
    )


@responses.activate  # type: ignore[misc]
def test_scrape_falls_back_to_next_data() -> None:
    """When no JSON-LD events exist, __NEXT_DATA__ events are yielded."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_next_data_only_html(), status=200)
    events = list(_make_scraper().scrape())

    assert len(events) == 1
    evt = events[0]
    assert evt.title == "NextData Only Show"
    # source_url is built from the perm_name.
    assert evt.source_url == "https://dice.fm/event/nextdata-only-event-1"
    assert evt.ticket_url == evt.source_url
    # Price comes from amount / 100 (cents -> dollars).
    assert evt.min_price == 15.0
    assert evt.max_price == 15.0
    # Images prefer landscape.
    assert evt.image_url == "https://dice-media.imgix.net/landscape.jpg"
    # Artists come from summary_lineup.top_artists, headliner first.
    assert evt.artists == ["Next Artist One", "Next Artist Two"]
    # Description copied from about.description.
    assert evt.description == "A show sourced from NEXT_DATA."
    # Timezone preserved.
    assert evt.starts_at.utcoffset() == timedelta(hours=-4)


# --------------------------------------------------------- error paths


@responses.activate  # type: ignore[misc]
def test_scrape_raises_when_no_data_sources() -> None:
    """Page with neither JSON-LD nor __NEXT_DATA__ raises DiceScraperError."""
    empty_html = "<html><body>Nothing here.</body></html>"
    responses.add(responses.GET, DC9_VENUE_URL, body=empty_html, status=200)
    with pytest.raises(DiceScraperError):
        list(_make_scraper().scrape())


@responses.activate  # type: ignore[misc]
def test_scrape_skips_malformed_event_block(caplog: pytest.LogCaptureFixture) -> None:
    """A block of invalid JSON is logged and skipped; good events survive."""
    responses.add(responses.GET, DC9_VENUE_URL, body=_load_fixture(), status=200)
    with caplog.at_level("WARNING"):
        events = list(_make_scraper().scrape())
    # The fixture deliberately includes one malformed JSON-LD block.
    assert any("malformed JSON-LD" in rec.message for rec in caplog.records)
    # And the two valid events still come through.
    assert len(events) == 2


@responses.activate  # type: ignore[misc]
def test_scrape_handles_http_error() -> None:
    """A non-200 response raises DiceScraperError."""
    responses.add(responses.GET, DC9_VENUE_URL, body="forbidden", status=403)
    with pytest.raises(DiceScraperError):
        list(_make_scraper().scrape())


def test_scrape_retries_once_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call raises ConnectionError; second returns the fixture HTML."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        dice_module.time, "sleep", lambda seconds: sleeps.append(seconds)
    )

    attempts = {"count": 0}

    class _FakeResponse:
        """Tiny stand-in for a requests.Response with just what Dice needs."""

        status_code = 200
        text = _load_fixture()

    def fake_get(self: Any, url: str, **kwargs: Any) -> _FakeResponse:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise requests.ConnectionError("boom")
        return _FakeResponse()

    monkeypatch.setattr(requests.Session, "get", fake_get)

    events = list(_make_scraper().scrape())
    assert attempts["count"] == 2
    assert len(events) == 2
    # Retry sleep is exactly the configured backoff.
    assert dice_module.CONNECTION_RETRY_BACKOFF_SECONDS in sleeps


def test_scrape_respects_rate_limit_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive fetches on the same session sleep ≥ 2s between them.

    Simulates a runner that hits two Dice venues back-to-back with the
    same scraper session. The rate-limit guard should insert a sleep on
    the second call.
    """
    sleeps: list[float] = []
    monkeypatch.setattr(dice_module.time, "sleep", lambda s: sleeps.append(s))

    # monotonic advances by 0.1s each call so the second fetch sees a
    # small elapsed and requests ~1.9s of sleep.
    tick = {"t": 0.0}

    def fake_monotonic() -> float:
        tick["t"] += 0.1
        return tick["t"]

    monkeypatch.setattr(dice_module.time, "monotonic", fake_monotonic)

    class _FakeResponse:
        status_code = 200
        text = _load_fixture()

    def fake_get(self: Any, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse()

    monkeypatch.setattr(requests.Session, "get", fake_get)

    scraper = _make_scraper()
    # Trigger two fetches manually via the internal helper so we aren't
    # invoking scrape() twice (which would double-yield events).
    scraper._fetch(DC9_VENUE_URL)
    scraper._fetch(DC9_VENUE_URL)

    assert sleeps, "expected at least one throttle sleep"
    assert max(sleeps) >= dice_module.INTER_REQUEST_DELAY_SECONDS - 0.5
