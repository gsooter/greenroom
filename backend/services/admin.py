"""Admin business logic — scraper audit and manual triggers.

Admin endpoints are gated by a shared secret (``ADMIN_SECRET_KEY``)
rather than a user JWT so operational tasks can be run from a CI job
or an on-call terminal without a real user session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from backend.data.models.scraper import ScraperRun, ScraperRunStatus
from backend.data.repositories import scraper_runs as runs_repo
from backend.scraper.config.venues import (
    VenueScraperConfig,
    get_enabled_configs,
    get_venue_config,
)
from backend.scraper.runner import run_scraper_for_venue

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def list_scraper_runs(
    session: Session,
    *,
    venue_slug: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[ScraperRun], int]:
    """List scraper runs, newest first, with optional filters.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Optional venue slug to scope the listing.
        status: Optional status string (``success``, ``partial``, ``failed``).
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (runs list, total count).

    Raises:
        ValidationError: If ``per_page`` exceeds 100 or ``status`` is not
            a valid :class:`ScraperRunStatus` value.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    status_enum: ScraperRunStatus | None = None
    if status is not None:
        try:
            status_enum = ScraperRunStatus(status)
        except ValueError as exc:
            allowed = ", ".join(s.value for s in ScraperRunStatus)
            raise ValidationError(f"status must be one of: {allowed}") from exc

    return runs_repo.list_scraper_runs(
        session,
        venue_slug=venue_slug,
        status=status_enum,
        page=page,
        per_page=per_page,
    )


def trigger_scraper_run(session: Session, venue_slug: str) -> dict[str, Any]:
    """Synchronously run the scraper for a single venue.

    Meant for manual ops (backfills, debugging, post-fix verification).
    Nightly production runs still go through the Celery task; this is
    the same code path — it just bypasses the scheduler.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue to scrape.

    Returns:
        The dict returned by :func:`run_scraper_for_venue`, containing
        status, event counts, and duration.

    Raises:
        NotFoundError: If no venue config exists for the slug, or the
            venue is present but disabled.
    """
    config = _require_enabled_venue(venue_slug)
    return run_scraper_for_venue(session, config)


def summarize_fleet() -> dict[str, Any]:
    """Return a static summary of the configured scraper fleet.

    Includes per-region venue counts and total enabled/disabled venues.
    Does not touch the database — it's a pure read of the in-code
    config, suitable for a health dashboard.

    Returns:
        Dictionary with ``total``, ``enabled``, and per-region counts.
    """
    enabled = get_enabled_configs()
    by_region: dict[str, int] = {}
    for config in enabled:
        by_region[config.region] = by_region.get(config.region, 0) + 1

    return {
        "enabled": len(enabled),
        "by_region": by_region,
        "venues": [
            {
                "slug": c.venue_slug,
                "display_name": c.display_name,
                "region": c.region,
                "city_slug": c.city_slug,
                "scraper_class": c.scraper_class,
            }
            for c in enabled
        ],
    }


def serialize_scraper_run(run: ScraperRun) -> dict[str, Any]:
    """Serialize a :class:`ScraperRun` for the admin API response.

    Args:
        run: The scraper run to serialize.

    Returns:
        Dictionary representation of the run.
    """
    return {
        "id": str(run.id),
        "venue_slug": run.venue_slug,
        "scraper_class": run.scraper_class,
        "status": run.status.value,
        "event_count": run.event_count,
        "started_at": run.started_at.isoformat(),
        "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
        "duration_seconds": run.duration_seconds,
        "error_message": run.error_message,
        "metadata": run.metadata_json or {},
    }


def _require_enabled_venue(venue_slug: str) -> VenueScraperConfig:
    """Look up a venue config by slug and require it to be enabled.

    Args:
        venue_slug: Slug of the venue.

    Returns:
        The :class:`VenueScraperConfig`.

    Raises:
        NotFoundError: If no config exists for the slug, or the venue is
            present but disabled.
    """
    config = get_venue_config(venue_slug)
    if config is None:
        raise NotFoundError(
            code="VENUE_CONFIG_NOT_FOUND",
            message=f"No scraper config for venue '{venue_slug}'",
        )
    if not config.enabled:
        raise NotFoundError(
            code="VENUE_CONFIG_DISABLED",
            message=f"Scraper for venue '{venue_slug}' is disabled.",
        )
    return config
