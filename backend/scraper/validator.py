"""Post-scrape validation and alerting.

Checks scraper results against historical averages and fires alerts
when anomalies are detected. Three independent signals are evaluated:

* Zero results — the scrape returned nothing. Always an error.
* Event count drop — the scrape returned >60% fewer events than the
  30-run average. Fires a warning (Decision 006).
* Stale data — recent successful runs ingested no new events for
  several consecutive runs in a row. Fires a warning. Catches the
  silent-failure mode where the venue page still loads and parses,
  but the listings have stopped updating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.logging import get_logger
from backend.data.models.scraper import ScraperRun, ScraperRunStatus
from backend.data.repositories import scraper_runs as runs_repo
from backend.scraper.notifier import send_alert

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

DROP_THRESHOLD = 0.6
"""A drop greater than this fraction of the 30-run average triggers a warning."""

STALE_DATA_RUN_THRESHOLD = 7
"""Consecutive successful runs with zero new ingests that flips to "stale".

A nightly scraper that ingests zero new events for a week looks
indistinguishable from a working scraper if you only watch run status.
This signal closes that gap.
"""


def validate_scraper_result(
    session: Session,
    *,
    venue_slug: str,
    event_count: int,
) -> bool:
    """Validate a scraper run's results against historical data.

    Evaluates three independent signals — zero results, event-count
    drop, and stale data — and emits alerts as appropriate. Stale-data
    is a non-blocking warning so it does not affect the boolean
    return.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue that was scraped.
        event_count: Number of events returned by the scraper.

    Returns:
        True when no blocking anomaly was detected (zero results or
        event-count drop). False otherwise.
    """
    if event_count == 0:
        send_alert(
            title=f"Zero results: {venue_slug}",
            message=(
                f"Scraper for '{venue_slug}' returned zero events. "
                f"The venue page may be down or the scraper may be broken."
            ),
            severity="error",
            details={"venue": venue_slug, "event_count": 0},
            alert_key=f"zero_results:{venue_slug}",
            cooldown_hours=12.0,
            session=session,
        )
        return False

    drop_alert_fired = _check_event_drop(
        session, venue_slug=venue_slug, event_count=event_count
    )
    _check_stale_data(session, venue_slug=venue_slug)
    return not drop_alert_fired


def _check_event_drop(
    session: Session,
    *,
    venue_slug: str,
    event_count: int,
) -> bool:
    """Compare ``event_count`` against the historical average and alert if low.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue that was scraped.
        event_count: Number of events returned by the scraper.

    Returns:
        True when a drop alert was fired, False otherwise (no history,
        zero historical average, or count within tolerance).
    """
    average = runs_repo.get_average_event_count(session, venue_slug)

    if average is None:
        logger.info("No historical data for '%s', skipping drop check.", venue_slug)
        return False

    if average == 0:
        return False

    drop_fraction = 1.0 - (event_count / average)

    if drop_fraction > DROP_THRESHOLD:
        send_alert(
            title=f"Event count drop: {venue_slug}",
            message=(
                f"Scraper for '{venue_slug}' returned {event_count} events, "
                f"which is a {drop_fraction:.0%} drop from the 30-run average "
                f"of {average:.0f}."
            ),
            severity="warning",
            details={
                "venue": venue_slug,
                "event_count": event_count,
                "average": f"{average:.0f}",
                "drop": f"{drop_fraction:.0%}",
            },
            alert_key=f"event_drop:{venue_slug}",
            cooldown_hours=12.0,
            session=session,
        )
        return True

    logger.info(
        "Validation passed for '%s': %d events (avg %.0f).",
        venue_slug,
        event_count,
        average,
    )
    return False


def _check_stale_data(
    session: Session,
    *,
    venue_slug: str,
) -> None:
    """Alert when a venue's recent runs succeed but ingest no new events.

    Pulls the most recent successful runs and inspects each row's
    ``metadata_json["created"]`` count. When the last
    :data:`STALE_DATA_RUN_THRESHOLD` successful runs all created zero
    events, the listings page is most likely frozen and the operator
    should investigate. Failures and partial runs are skipped — a
    pure-success streak is the signal we care about.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue to evaluate.
    """
    # Pull a generous window so we can ignore non-success rows in
    # between (e.g. a one-off failure two weeks ago shouldn't reset
    # the stale-data check forever).
    runs = runs_repo.get_recent_runs(
        session, venue_slug, limit=STALE_DATA_RUN_THRESHOLD * 3
    )
    successful = [r for r in runs if r.status is ScraperRunStatus.SUCCESS][
        :STALE_DATA_RUN_THRESHOLD
    ]
    if len(successful) < STALE_DATA_RUN_THRESHOLD:
        return
    if not all(_created_count(r) == 0 for r in successful):
        return

    send_alert(
        title=f"Stale data: {venue_slug}",
        message=(
            f"The last {STALE_DATA_RUN_THRESHOLD} successful runs for "
            f"'{venue_slug}' ingested zero new events. The page is loading "
            f"and parsing, but no fresh listings are appearing — the venue's "
            f"calendar may be stuck or the scraper may be locked to a "
            f"stale view."
        ),
        severity="warning",
        details={
            "venue": venue_slug,
            "consecutive_zero_creates": STALE_DATA_RUN_THRESHOLD,
        },
        alert_key=f"stale_data:{venue_slug}",
        cooldown_hours=48.0,
        session=session,
    )


def _created_count(run: ScraperRun) -> int:
    """Pull the ``created`` count out of a run's metadata, defaulting to 0.

    Args:
        run: The ScraperRun to inspect.

    Returns:
        The number of new events created by that run, or 0 if the
        metadata is missing or malformed.
    """
    metadata = run.metadata_json or {}
    value = metadata.get("created")
    return value if isinstance(value, int) else 0
