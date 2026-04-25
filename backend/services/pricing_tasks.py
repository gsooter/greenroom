"""Celery tasks that fan out the multi-source pricing orchestrator.

The nightly :func:`refresh_all_event_pricing` task walks upcoming
events stalest-first, runs every registered Tier A and Tier B provider,
and persists the resulting snapshots and pricing-link rows. Pass
``force=True`` so the cooldown gate (which protects manual click-fests)
never short-circuits the cron.

Kept separate from :mod:`backend.services.tickets` so that pure module
has no Celery imports — the orchestrator stays unit-testable without
touching the broker.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import events as events_repo
from backend.services.tickets import refresh_event_pricing

logger = get_logger(__name__)

BATCH_SIZE = 500
"""Maximum events processed in one sweep run.

Sized so the SeatGeek and Ticketmaster API budgets aren't burned in a
single pass even at peak catalog size — the next morning's run picks
up the tail because :func:`backend.data.repositories.events
.list_events_for_pricing_sweep` re-orders by stalest-first.
"""


@shared_task(
    name="backend.services.pricing_tasks.refresh_all_event_pricing",
)  # type: ignore[untyped-decorator]
def refresh_all_event_pricing() -> dict[str, Any]:
    """Refresh pricing for every upcoming event in one batched pass.

    Owns its own session so the orchestrator can keep its session
    parameter typed as :class:`sqlalchemy.orm.Session` without each
    caller having to thread session lifecycle through the task layer.
    Per-event failures are caught and counted rather than re-raised so
    one broken event (e.g., a deleted venue causing a relationship
    miss) does not stop the rest of the catalog from getting fresh
    prices.

    Returns:
        Summary dict with keys ``processed`` (events visited),
        ``succeeded`` (events whose orchestrator returned without
        raising), ``errors`` (events that raised mid-refresh),
        ``quotes_persisted`` (sum of snapshots written across the
        batch), ``links_upserted`` (sum of pricing-link rows touched),
        ``provider_errors`` (deduped list of provider names that raised
        on at least one event).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            events = events_repo.list_events_for_pricing_sweep(
                session, limit=BATCH_SIZE
            )
            if not events:
                return {
                    "processed": 0,
                    "succeeded": 0,
                    "errors": 0,
                    "quotes_persisted": 0,
                    "links_upserted": 0,
                    "provider_errors": [],
                }

            succeeded = 0
            errors = 0
            quotes_persisted = 0
            links_upserted = 0
            provider_errors: set[str] = set()

            for event in events:
                try:
                    result = refresh_event_pricing(session, event, force=True)
                    succeeded += 1
                    quotes_persisted += result.quotes_persisted
                    links_upserted += result.links_upserted
                    provider_errors.update(result.provider_errors)
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "pricing_sweep_event_failed",
                        extra={
                            "event_id": str(event.id),
                            "error": str(exc),
                        },
                    )
                    # Mid-batch failures (e.g., a flush violating a
                    # constraint) would otherwise poison subsequent
                    # writes; rolling back keeps the session usable.
                    session.rollback()

            session.commit()
            return {
                "processed": len(events),
                "succeeded": succeeded,
                "errors": errors,
                "quotes_persisted": quotes_persisted,
                "links_upserted": links_upserted,
                "provider_errors": sorted(provider_errors),
            }
        except Exception:
            session.rollback()
            raise
