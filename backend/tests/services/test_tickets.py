"""Unit tests for the multi-source pricing orchestrator.

The orchestrator is the seam between the provider abstraction and the
pricing tables. Tests stub the providers (no HTTP) and the events
repository (no Postgres) so the focus stays on the orchestration
contract: cooldown gating, per-provider error isolation, snapshot vs
pricing-link decoupling, and the response shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import requests

from backend.pricing.base import BasePricingProvider, PriceQuote
from backend.services import tickets as tickets_service


@dataclass
class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Attributes:
        id: Event UUID.
        prices_refreshed_at: Last successful sweep timestamp; powers the
            cooldown gate.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    prices_refreshed_at: datetime | None = None


class _StaticProvider(BasePricingProvider):
    """Provider that always returns a configured quote (or ``None``)."""

    def __init__(self, name: str, quote: PriceQuote | None) -> None:
        """Initialize with a fixed name and quote.

        Args:
            name: Provider identifier surfaced on the resulting quote.
            quote: The :class:`PriceQuote` (or ``None``) to return on
                every :meth:`fetch` call.
        """
        self.name = name
        self._quote = quote

    def fetch(self, event: Any) -> PriceQuote | None:
        """Return the pre-configured quote regardless of event.

        Args:
            event: Ignored.

        Returns:
            The fixed quote supplied at construction.
        """
        return self._quote


class _RaisingProvider(BasePricingProvider):
    """Provider that always raises a known exception type."""

    def __init__(self, name: str, exc: Exception) -> None:
        """Initialize with a fixed name and exception.

        Args:
            name: Provider identifier.
            exc: The exception to raise on each call.
        """
        self.name = name
        self._exc = exc

    def fetch(self, event: Any) -> PriceQuote | None:
        """Raise the configured exception.

        Args:
            event: Ignored.

        Raises:
            Exception: The configured exception instance.
        """
        raise self._exc


@pytest.fixture
def patched_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Patch the events repository with capturing fakes.

    Returns:
        Dict with three lists: ``snapshots``, ``links``, ``stamps`` —
        each entry is the kwargs dict the corresponding repo function
        was called with. Lets tests assert exactly what the
        orchestrator persisted without standing up a real session.
    """
    snapshots: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    stamps: list[dict[str, Any]] = []

    def fake_create_snapshot(_session: Any, **kwargs: Any) -> object:
        snapshots.append(kwargs)
        return object()

    def fake_upsert_link(_session: Any, **kwargs: Any) -> object:
        links.append(kwargs)
        return object()

    def fake_stamp(
        _session: Any,
        event_id: uuid.UUID,
        *,
        refreshed_at: datetime | None = None,
    ) -> datetime:
        stamp = refreshed_at or datetime.now(UTC)
        stamps.append({"event_id": event_id, "refreshed_at": stamp})
        return stamp

    monkeypatch.setattr(
        "backend.services.tickets.events_repo.create_ticket_snapshot",
        fake_create_snapshot,
    )
    monkeypatch.setattr(
        "backend.services.tickets.events_repo.upsert_pricing_link",
        fake_upsert_link,
    )
    monkeypatch.setattr(
        "backend.services.tickets.events_repo.stamp_prices_refreshed_at",
        fake_stamp,
    )
    return {"snapshots": snapshots, "links": links, "stamps": stamps}


def test_cooldown_short_circuits_when_recently_refreshed(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A recent refresh inside the cooldown window skips all providers.

    Two visitors mashing the refresh button must share one cooldown so
    the upstream API budget can't be burned by parallel clients.
    """
    last = datetime.now(UTC) - timedelta(minutes=2)
    event = _FakeEvent(prices_refreshed_at=last)
    provider = _StaticProvider(
        "seatgeek",
        PriceQuote(source="seatgeek", min_price=40.0, buy_url="https://x.test"),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert result.cooldown_active is True
    assert result.quotes_persisted == 0
    assert result.links_upserted == 0
    assert result.refreshed_at == last
    assert patched_repo["snapshots"] == []
    assert patched_repo["links"] == []
    assert patched_repo["stamps"] == []


def test_force_bypasses_cooldown(
    patched_repo: dict[str, list[Any]],
) -> None:
    """``force=True`` runs providers even inside the cooldown window.

    The daily Celery sweep passes ``force=True`` so the cron always
    re-fetches; the manual button passes ``False`` so the cooldown
    holds.
    """
    last = datetime.now(UTC) - timedelta(minutes=1)
    event = _FakeEvent(prices_refreshed_at=last)
    provider = _StaticProvider(
        "seatgeek",
        PriceQuote(
            source="seatgeek",
            min_price=40.0,
            max_price=80.0,
            buy_url="https://x.test",
        ),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
        force=True,
    )

    assert result.cooldown_active is False
    assert result.quotes_persisted == 1
    assert result.links_upserted == 1
    assert len(patched_repo["stamps"]) == 1


def test_naive_prices_refreshed_at_treated_as_utc(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A timezone-naive ``prices_refreshed_at`` is interpreted as UTC.

    Older rows could have been written before we made timestamps
    tz-aware everywhere; comparing naive-vs-aware would crash. The
    cooldown gate normalizes defensively rather than letting the
    refresh path raise.
    """
    last = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    event = _FakeEvent(prices_refreshed_at=last)

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[],
    )

    assert result.cooldown_active is True


def test_snapshot_persisted_when_quote_has_price_signal(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A quote with any numeric field becomes a snapshot row.

    The ML buy-now layer trains on this history table — capturing every
    refresh that produced a number is the whole point of the snapshot
    table existing separately from the pricing link.
    """
    event = _FakeEvent()
    provider = _StaticProvider(
        "seatgeek",
        PriceQuote(
            source="seatgeek",
            min_price=42.5,
            max_price=120.0,
            average_price=75.0,
            listing_count=14,
            buy_url="https://seatgeek.test/x",
            raw={"upstream": "payload"},
        ),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert result.quotes_persisted == 1
    assert result.links_upserted == 1
    assert len(patched_repo["snapshots"]) == 1
    snap = patched_repo["snapshots"][0]
    assert snap["source"] == "seatgeek"
    assert snap["min_price"] == 42.5
    assert snap["max_price"] == 120.0
    assert snap["average_price"] == 75.0
    assert snap["listing_count"] == 14
    assert snap["raw_data"] == {"upstream": "payload"}


def test_url_only_quote_skips_snapshot_but_writes_link(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A quote with only a buy URL produces a link but no snapshot.

    TickPick falls into this bucket — it returns a deterministic search
    URL but never carries prices. We still want the link in the UI;
    snapshotting empty rows would just bloat the training set.
    """
    event = _FakeEvent()
    provider = _StaticProvider(
        "tickpick",
        PriceQuote(source="tickpick", buy_url="https://tickpick.test/search?q=x"),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert result.quotes_persisted == 0
    assert result.links_upserted == 1
    assert patched_repo["snapshots"] == []
    assert patched_repo["links"][0]["url"] == "https://tickpick.test/search?q=x"


def test_price_only_quote_writes_snapshot_but_no_link(
    patched_repo: dict[str, list[Any]],
) -> None:
    """Prices without a URL produce a snapshot but no pricing link.

    Some scrapers extract price ranges from schema.org without a clean
    deep link; we still want the historical signal even though there's
    no clickable surface.
    """
    event = _FakeEvent()
    provider = _StaticProvider(
        "comet_ping_pong",
        PriceQuote(source="comet_ping_pong", min_price=15.0, max_price=20.0),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert result.quotes_persisted == 1
    assert result.links_upserted == 0
    assert patched_repo["links"] == []


def test_none_quote_skipped_silently(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A provider returning ``None`` is a no-op for that pass.

    Providers abstain (return ``None``) when the event isn't on their
    surface — the orchestrator must not record this as an error or
    persist anything.
    """
    event = _FakeEvent()
    provider = _StaticProvider("dice", None)

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert result.quotes_persisted == 0
    assert result.links_upserted == 0
    assert result.provider_errors == ()


def test_provider_error_is_isolated(
    patched_repo: dict[str, list[Any]],
) -> None:
    """One provider raising does not stop the others or kill the sweep.

    A SeatGeek 503 must not drop a Ticketmaster quote that arrived
    before it. The failed provider's name surfaces in
    ``provider_errors`` so the UI can show "Some providers were
    unavailable".
    """
    good = _StaticProvider(
        "ticketmaster",
        PriceQuote(
            source="ticketmaster",
            min_price=55.0,
            buy_url="https://tm.test/x",
        ),
    )
    bad = _RaisingProvider("seatgeek", requests.ConnectionError("boom"))

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=_FakeEvent(),  # type: ignore[arg-type]
        providers=[bad, good],
    )

    assert result.quotes_persisted == 1
    assert result.links_upserted == 1
    assert result.provider_errors == ("seatgeek",)


def test_value_error_treated_as_provider_failure(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A provider raising :class:`ValueError` (e.g., bad JSON) is caught.

    Parse errors should be a soft failure — note the provider in
    ``provider_errors`` and keep going rather than 500-ing the whole
    refresh.
    """
    bad = _RaisingProvider("seatgeek", ValueError("bad payload"))

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=_FakeEvent(),  # type: ignore[arg-type]
        providers=[bad],
    )

    assert result.provider_errors == ("seatgeek",)
    assert result.quotes_persisted == 0
    assert result.links_upserted == 0


def test_unexpected_exception_propagates(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A genuinely unexpected exception is not swallowed.

    ``RuntimeError`` and friends usually mean a real bug — masking
    them as soft "provider unavailable" errors would hide regressions.
    """
    bad = _RaisingProvider("seatgeek", RuntimeError("programmer error"))

    with pytest.raises(RuntimeError):
        tickets_service.refresh_event_pricing(
            session=None,  # type: ignore[arg-type]
            event=_FakeEvent(),  # type: ignore[arg-type]
            providers=[bad],
        )


def test_refreshed_at_is_stamped_after_successful_sweep(
    patched_repo: dict[str, list[Any]],
) -> None:
    """A successful sweep stamps ``prices_refreshed_at`` exactly once.

    The cooldown gate and the "Updated X ago" label both read off this
    column; double-stamps would be harmless but a missing stamp would
    let the next visitor immediately re-burn the API budget.
    """
    event = _FakeEvent()
    provider = _StaticProvider(
        "seatgeek",
        PriceQuote(source="seatgeek", min_price=40.0, buy_url="https://x.test"),
    )

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[provider],
    )

    assert len(patched_repo["stamps"]) == 1
    assert patched_repo["stamps"][0]["event_id"] == event.id
    assert result.refreshed_at == patched_repo["stamps"][0]["refreshed_at"]


def test_empty_provider_set_still_stamps_refreshed_at(
    patched_repo: dict[str, list[Any]],
) -> None:
    """An empty provider list still stamps so the cooldown engages.

    Otherwise a misconfigured registry could let every refresh skip
    the stamp and the cooldown gate would never trip.
    """
    event = _FakeEvent()

    result = tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
        providers=[],
    )

    assert result.cooldown_active is False
    assert result.quotes_persisted == 0
    assert result.links_upserted == 0
    assert len(patched_repo["stamps"]) == 1


def test_default_provider_set_used_when_none_passed(
    monkeypatch: pytest.MonkeyPatch,
    patched_repo: dict[str, list[Any]],
) -> None:
    """Omitting ``providers`` resolves to the registry's canonical set.

    Production never passes a provider list — the orchestrator pulls
    from :func:`backend.pricing.registry.get_providers`. The seam is
    important; tests inject explicit lists, but production must hit
    the real inventory.
    """
    captured: list[Any] = []

    def fake_get_providers() -> list[BasePricingProvider]:
        provider = _StaticProvider("seatgeek", None)
        captured.append(provider)
        return [provider]

    monkeypatch.setattr(
        "backend.services.tickets.registry.get_providers",
        fake_get_providers,
    )

    tickets_service.refresh_event_pricing(
        session=None,  # type: ignore[arg-type]
        event=_FakeEvent(),  # type: ignore[arg-type]
    )

    assert len(captured) == 1


def test_serialize_pricing_state_joins_snapshots_and_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The response shape merges latest snapshot + link rows by source.

    The frontend renders one row per source; the service must hand it
    a single flat list keyed by ``source`` rather than two parallel
    arrays the UI then has to join.
    """

    @dataclass
    class _FakeSnap:
        source: str
        min_price: float | None
        max_price: float | None
        average_price: float | None
        listing_count: int | None

    @dataclass
    class _FakeLink:
        source: str
        url: str
        affiliate_url: str | None
        is_active: bool
        currency: str
        last_seen_at: datetime | None
        last_active_at: datetime | None

    snaps = [
        _FakeSnap(
            source="seatgeek",
            min_price=40.0,
            max_price=80.0,
            average_price=60.0,
            listing_count=14,
        ),
        _FakeSnap(
            source="ticketmaster",
            min_price=55.0,
            max_price=120.0,
            average_price=None,
            listing_count=None,
        ),
    ]
    seen = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    active = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    links = [
        _FakeLink(
            source="seatgeek",
            url="https://seatgeek.test/x",
            affiliate_url="https://seatgeek.test/x?aff=greenroom",
            is_active=True,
            currency="USD",
            last_seen_at=seen,
            last_active_at=active,
        ),
        _FakeLink(
            source="ticketmaster",
            url="https://tm.test/x",
            affiliate_url=None,
            is_active=True,
            currency="USD",
            last_seen_at=seen,
            last_active_at=active,
        ),
        _FakeLink(
            source="tickpick",
            url="https://tickpick.test/search?q=x",
            affiliate_url=None,
            is_active=True,
            currency="USD",
            last_seen_at=seen,
            last_active_at=None,
        ),
    ]

    monkeypatch.setattr(
        "backend.services.tickets.events_repo.list_latest_snapshots_by_source",
        lambda _s, _e: snaps,
    )
    monkeypatch.setattr(
        "backend.services.tickets.events_repo.list_pricing_links",
        lambda _s, _e: links,
    )

    refreshed = datetime(2026, 4, 25, 14, 31, tzinfo=UTC)
    event = _FakeEvent(prices_refreshed_at=refreshed)

    payload = tickets_service.serialize_pricing_state(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
    )

    assert payload["refreshed_at"] == refreshed.isoformat()
    assert len(payload["sources"]) == 3
    by_source = {row["source"]: row for row in payload["sources"]}

    assert by_source["seatgeek"]["min_price"] == 40.0
    assert by_source["seatgeek"]["max_price"] == 80.0
    assert by_source["seatgeek"]["average_price"] == 60.0
    assert by_source["seatgeek"]["listing_count"] == 14
    assert by_source["seatgeek"]["affiliate_url"].endswith("aff=greenroom")

    assert by_source["ticketmaster"]["min_price"] == 55.0

    # tickpick has a link but no snapshot — every numeric field is None,
    # but the buy URL still surfaces so the UI can render the CTA.
    tp = by_source["tickpick"]
    assert tp["min_price"] is None
    assert tp["max_price"] is None
    assert tp["average_price"] is None
    assert tp["listing_count"] is None
    assert tp["buy_url"] == "https://tickpick.test/search?q=x"
    assert tp["last_active_at"] is None


def test_serialize_pricing_state_handles_missing_refresh_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event that's never been refreshed serializes ``None``.

    Brand-new events haven't been swept yet; the UI distinguishes
    "never refreshed" from "refreshed long ago" so the value must be
    ``None`` rather than a stale fallback.
    """
    monkeypatch.setattr(
        "backend.services.tickets.events_repo.list_latest_snapshots_by_source",
        lambda _s, _e: [],
    )
    monkeypatch.setattr(
        "backend.services.tickets.events_repo.list_pricing_links",
        lambda _s, _e: [],
    )

    event = _FakeEvent(prices_refreshed_at=None)
    payload = tickets_service.serialize_pricing_state(
        session=None,  # type: ignore[arg-type]
        event=event,  # type: ignore[arg-type]
    )
    assert payload["refreshed_at"] is None
    assert payload["sources"] == []
