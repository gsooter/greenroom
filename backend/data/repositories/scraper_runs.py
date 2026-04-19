"""Repository functions for scraper run tracking.

Provides queries for logging scraper executions and retrieving
historical data used by the validator for anomaly detection.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.data.models.scraper import ScraperRun, ScraperRunStatus


def create_scraper_run(
    session: Session,
    *,
    venue_slug: str,
    scraper_class: str,
    status: ScraperRunStatus,
    event_count: int,
    started_at: datetime,
    finished_at: datetime | None = None,
    duration_seconds: float | None = None,
    error_message: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> ScraperRun:
    """Log a scraper run.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue that was scraped.
        scraper_class: Fully qualified class name of the scraper.
        status: Outcome of the scraper run.
        event_count: Number of events returned.
        started_at: When the run started.
        finished_at: When the run completed.
        duration_seconds: Total run duration in seconds.
        error_message: Error message if the run failed.
        metadata_json: Additional run metadata.

    Returns:
        The newly created ScraperRun instance.
    """
    run = ScraperRun(
        venue_slug=venue_slug,
        scraper_class=scraper_class,
        status=status,
        event_count=event_count,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        error_message=error_message,
        metadata_json=metadata_json,
    )
    session.add(run)
    session.flush()
    return run


def get_recent_runs(
    session: Session,
    venue_slug: str,
    *,
    limit: int = 30,
) -> list[ScraperRun]:
    """Fetch the most recent scraper runs for a venue.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue.
        limit: Maximum number of runs to return. Defaults to 30.

    Returns:
        List of ScraperRun instances, newest first.
    """
    stmt = (
        select(ScraperRun)
        .where(ScraperRun.venue_slug == venue_slug)
        .order_by(ScraperRun.started_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def get_average_event_count(
    session: Session,
    venue_slug: str,
    *,
    last_n_runs: int = 30,
) -> float | None:
    """Calculate the average event count over the last N successful runs.

    Used by the validator to detect anomalies. A current run's event
    count is compared against this average — a >60% drop triggers an alert.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue.
        last_n_runs: Number of recent successful runs to average over.
            Defaults to 30.

    Returns:
        The average event count, or None if no successful runs exist.
    """
    subquery = (
        select(ScraperRun.event_count)
        .where(
            ScraperRun.venue_slug == venue_slug,
            ScraperRun.status == ScraperRunStatus.SUCCESS,
        )
        .order_by(ScraperRun.started_at.desc())
        .limit(last_n_runs)
        .subquery()
    )
    stmt = select(func.avg(subquery.c.event_count))
    result = session.execute(stmt).scalar_one_or_none()
    return float(result) if result is not None else None


def get_last_successful_run(
    session: Session,
    venue_slug: str,
) -> ScraperRun | None:
    """Fetch the most recent successful scraper run for a venue.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue.

    Returns:
        The most recent successful ScraperRun, or None.
    """
    stmt = (
        select(ScraperRun)
        .where(
            ScraperRun.venue_slug == venue_slug,
            ScraperRun.status == ScraperRunStatus.SUCCESS,
        )
        .order_by(ScraperRun.started_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def list_scraper_runs(
    session: Session,
    *,
    venue_slug: str | None = None,
    status: ScraperRunStatus | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[ScraperRun], int]:
    """List scraper runs, newest first, with optional filters.

    Used by the admin dashboard to audit scraper health across the
    fleet or drill into a single venue's history.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Optional venue slug to scope the listing.
        status: Optional status filter.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 50.

    Returns:
        Tuple of (runs list, total count).
    """
    base = select(ScraperRun)
    if venue_slug is not None:
        base = base.where(ScraperRun.venue_slug == venue_slug)
    if status is not None:
        base = base.where(ScraperRun.status == status)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base.order_by(ScraperRun.started_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    runs = list(session.execute(stmt).scalars().all())
    return runs, total


def count_failed_runs_since(
    session: Session,
    venue_slug: str,
    since: datetime,
) -> int:
    """Count failed scraper runs for a venue since a given time.

    Useful for escalation logic — e.g., alert if >3 consecutive failures.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue.
        since: Count failures after this datetime.

    Returns:
        Number of failed runs since the given time.
    """
    stmt = select(func.count()).where(
        ScraperRun.venue_slug == venue_slug,
        ScraperRun.status == ScraperRunStatus.FAILED,
        ScraperRun.started_at >= since,
    )
    return session.execute(stmt).scalar_one()
