"""Celery tasks that drive admin-triggered artist catalog hydration.

Owns the unattended scheduled hydration (Decision 069) and the
manual-trigger task the admin dashboard fires via ``send_task``. Both
delegate to :func:`backend.services.artist_hydration.mass_hydrate`,
so the safety controls (depth, threshold, per-call cap, daily cap)
apply identically to scheduled and manual invocations.

Lives in its own module so importing the pure service in tests does
not pull Celery into the import graph.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger

logger = get_logger(__name__)

NIGHTLY_OPERATOR_EMAIL: str = "scheduler@greenroom.local"
"""Email recorded in the audit log for the unattended nightly run.

Distinct from human operators so audit grep can separate them.
"""


@shared_task(
    name="backend.services.artist_hydration_tasks.mass_hydrate_task",
)  # type: ignore[untyped-decorator]
def mass_hydrate_task(admin_email: str = NIGHTLY_OPERATOR_EMAIL) -> dict[str, Any]:
    """Run the largest safe set of hydrations in one pass.

    Both the nightly beat schedule and the admin "Mass hydrate now"
    button enqueue this task. Owns its own session: the underlying
    service commits per-source so partial progress survives a
    mid-run failure, and the outer try/except guards an unexpected
    exception from leaking past the worker.

    Args:
        admin_email: Email recorded in every audit log row this run
            produces. Defaults to :data:`NIGHTLY_OPERATOR_EMAIL` so
            the beat-scheduled invocation needs no arguments; the
            manual button passes the operator's email.

    Returns:
        Summary dict suitable for surfacing in the Celery result
        backend or the admin UI's response payload.
    """
    # Local import to avoid circular module loading at worker boot —
    # the hydration service module itself does not pull Celery in.
    from backend.services.artist_hydration import mass_hydrate

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            summary = mass_hydrate(session, admin_email=admin_email)
            session.commit()
            payload = {
                "admin_email": admin_email,
                "sources_processed": summary.sources_processed,
                "sources_skipped": summary.sources_skipped,
                "artists_added": summary.artists_added,
                "daily_cap_reached": summary.daily_cap_reached,
                "per_source": summary.per_source,
            }
            logger.info("mass_hydrate_task_completed", extra=payload)
            return payload
        except Exception:
            session.rollback()
            raise
