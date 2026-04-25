"""Multi-source ticket pricing orchestrator.

Fans out :class:`~backend.pricing.base.BasePricingProvider` instances
across one event, persists each :class:`PriceQuote` as a snapshot
(append-only history for the future ML buy-now layer) and an upserted
:class:`EventPricingLink` (latest known buy URL per source), and
stamps :attr:`Event.prices_refreshed_at` so the cooldown gate and the
"Updated X ago" UI label both have a single source of truth.

Persistence happens here, not in the providers. Each provider only
fetches and parses; the service decides what's worth persisting and
when to short-circuit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import requests

from backend.core.logging import get_logger
from backend.data.repositories import events as events_repo
from backend.pricing import registry

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.events import Event
    from backend.pricing.base import BasePricingProvider, PriceQuote


logger = get_logger(__name__)


REFRESH_COOLDOWN = timedelta(minutes=5)
"""Per-event cooldown between manual refreshes.

Five minutes is short enough that someone reloading the page after a
real change still sees fresh data, and long enough that a misbehaving
client can't burn the upstream API budget. Set globally on
:attr:`Event.prices_refreshed_at` so the cooldown is shared across
every visitor — refreshing on one tab cools the button down on every
other open tab too, which is the explicit DB-backed semantics the
product asked for.
"""


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of a single :func:`refresh_event_pricing` call.

    Attributes:
        event_id: UUID of the event that was refreshed.
        refreshed_at: Timestamp written to ``events.prices_refreshed_at``
            on a successful run, or the previous value when the call
            short-circuited on the cooldown gate.
        cooldown_active: ``True`` when the call short-circuited because
            the previous refresh was inside :data:`REFRESH_COOLDOWN`.
        quotes_persisted: Number of providers whose quotes were turned
            into snapshot rows. Always ``0`` on a cooldown short-circuit.
        links_upserted: Number of :class:`EventPricingLink` rows the
            sweep created or updated.
        provider_errors: Provider names that raised mid-fetch. The sweep
            keeps going so a single broken upstream doesn't poison the
            whole refresh; this list goes into the response body so
            the UI can surface "Some providers were unavailable".
    """

    event_id: uuid.UUID
    refreshed_at: datetime
    cooldown_active: bool
    quotes_persisted: int
    links_upserted: int
    provider_errors: tuple[str, ...]


def refresh_event_pricing(
    session: Session,
    event: Event,
    *,
    force: bool = False,
    providers: list[BasePricingProvider] | None = None,
) -> RefreshResult:
    """Fan out every active provider against ``event`` and persist results.

    The orchestrator is intentionally simple: iterate, fetch, persist.
    Each provider gets its own try/except so a transient SeatGeek 503
    doesn't drop the Ticketmaster quote that arrived before it.
    Snapshots are always written when a quote carries any pricing
    field — that's what the future ML layer needs. Pricing-link rows
    are upserted whenever a quote carries a ``buy_url``, even with no
    inventory, so the link survives sold-out windows.

    Args:
        session: Active SQLAlchemy session. The caller commits.
        event: The event to refresh.
        force: When ``True``, skip the cooldown gate. The daily Celery
            sweep passes ``force=True``; manual refresh button passes
            ``False`` so concurrent users don't blow the upstream
            budget.
        providers: Optional explicit provider list. ``None`` resolves
            via :func:`backend.pricing.registry.get_providers` so
            production hits the canonical set; tests inject stubs.

    Returns:
        A :class:`RefreshResult` summarising what was persisted.
    """
    now = datetime.now(UTC)

    if not force and _is_in_cooldown(event, now):
        logger.info(
            "Skipping refresh for event=%s — cooldown active until %s",
            event.id,
            (event.prices_refreshed_at or now) + REFRESH_COOLDOWN,
        )
        return RefreshResult(
            event_id=event.id,
            refreshed_at=event.prices_refreshed_at or now,
            cooldown_active=True,
            quotes_persisted=0,
            links_upserted=0,
            provider_errors=(),
        )

    provider_set = providers if providers is not None else registry.get_providers()

    quotes_persisted = 0
    links_upserted = 0
    errors: list[str] = []

    for provider in provider_set:
        try:
            quote = provider.fetch(event)
        except (requests.RequestException, ValueError) as exc:
            logger.warning(
                "Provider %s failed for event=%s: %s",
                provider.name,
                event.id,
                exc,
            )
            errors.append(provider.name)
            continue

        if quote is None:
            continue

        if _has_price_signal(quote):
            events_repo.create_ticket_snapshot(
                session,
                event_id=event.id,
                source=quote.source,
                min_price=quote.min_price,
                max_price=quote.max_price,
                average_price=quote.average_price,
                listing_count=quote.listing_count,
                currency=quote.currency,
                raw_data=quote.raw or None,
            )
            quotes_persisted += 1

        if quote.buy_url:
            events_repo.upsert_pricing_link(
                session,
                event_id=event.id,
                source=quote.source,
                url=quote.buy_url,
                affiliate_url=quote.affiliate_url,
                is_active=quote.is_active,
                currency=quote.currency,
                seen_at=now,
            )
            links_upserted += 1

    refreshed_at = events_repo.stamp_prices_refreshed_at(
        session, event.id, refreshed_at=now
    )

    return RefreshResult(
        event_id=event.id,
        refreshed_at=refreshed_at,
        cooldown_active=False,
        quotes_persisted=quotes_persisted,
        links_upserted=links_upserted,
        provider_errors=tuple(errors),
    )


def _is_in_cooldown(event: Event, now: datetime) -> bool:
    """Return whether ``event`` was refreshed within the cooldown window.

    Args:
        event: The event under consideration.
        now: Reference timestamp; passed in so tests can pin the clock.

    Returns:
        ``True`` when ``prices_refreshed_at`` is set and within
        :data:`REFRESH_COOLDOWN` of ``now``.
    """
    last = event.prices_refreshed_at
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last < REFRESH_COOLDOWN


def _has_price_signal(quote: PriceQuote) -> bool:
    """Return whether the quote carries any number worth persisting.

    A snapshot row is only useful if at least one numeric field is
    populated — empty rows would just bloat the future ML training
    set. The pricing-link row is decoupled from this check, so a
    URL-only quote (TickPick search-link) still produces a buy link
    without a paired snapshot.

    Args:
        quote: The provider's quote.

    Returns:
        ``True`` when at least one of min/max/average/listing_count
        is non-``None``.
    """
    return (
        quote.min_price is not None
        or quote.max_price is not None
        or quote.average_price is not None
        or quote.listing_count is not None
    )


def serialize_pricing_state(session: Session, event: Event) -> dict[str, object]:
    """Serialize the per-source pricing state for an event response.

    Used by the manual-refresh endpoint and by the event-detail
    endpoint to render the multi-source pricing UI in one shape:
    ``{ refreshed_at, sources: [{ source, min, max, listing_count,
    buy_url, affiliate_url, is_active }] }``.

    Args:
        session: Active SQLAlchemy session.
        event: The event whose pricing to serialize.

    Returns:
        Dict shaped for direct return as a JSON body.
    """
    snapshots = events_repo.list_latest_snapshots_by_source(session, event.id)
    snapshots_by_source = {snap.source: snap for snap in snapshots}
    links = events_repo.list_pricing_links(session, event.id)

    sources: list[dict[str, object]] = []
    for link in links:
        snap = snapshots_by_source.get(link.source)
        sources.append(
            {
                "source": link.source,
                "buy_url": link.url,
                "affiliate_url": link.affiliate_url,
                "is_active": link.is_active,
                "currency": link.currency,
                "min_price": snap.min_price if snap else None,
                "max_price": snap.max_price if snap else None,
                "average_price": snap.average_price if snap else None,
                "listing_count": snap.listing_count if snap else None,
                "last_seen_at": link.last_seen_at.isoformat()
                if link.last_seen_at
                else None,
                "last_active_at": link.last_active_at.isoformat()
                if link.last_active_at
                else None,
            }
        )

    return {
        "refreshed_at": event.prices_refreshed_at.isoformat()
        if event.prices_refreshed_at
        else None,
        "sources": sources,
    }
