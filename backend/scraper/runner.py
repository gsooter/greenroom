"""Scraper runner — orchestrates scraper execution and event ingestion.

Runs scrapers for configured venues, ingests RawEvent results into
the database (deduplicating by external_id), logs scraper runs, and
triggers validation/alerts on anomalies.

This module is called by Celery tasks for scheduled execution and
can also be run directly for manual/debugging runs.
"""

import hashlib
import importlib
import re
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from sqlalchemy.orm import Session

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.models.scraper import ScraperRunStatus
from backend.data.repositories import events as events_repo
from backend.data.repositories import scraper_runs as runs_repo
from backend.data.repositories import venues as venues_repo
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper
from backend.scraper.config.venues import (
    VenueScraperConfig,
    get_enabled_configs,
    get_venue_config,
)
from backend.scraper.notifier import send_alert
from backend.scraper.validator import validate_scraper_result

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@shared_task(name="backend.scraper.runner.scrape_all_venues")  # type: ignore[untyped-decorator]
def scrape_all_venues() -> dict[str, Any]:
    """Celery task: run every enabled scraper and commit ingested events.

    Managed session lifecycle — the worker owns its own DB session and
    commits on success / rolls back on unhandled exception. Per-venue
    failures are caught inside :func:`run_scraper_for_venue` and logged
    as FAILED scraper_runs, so one broken venue never takes the whole
    nightly job down.

    Returns:
        The per-venue results dict from :func:`run_all_scrapers`,
        suitable for Flower inspection or Celery result backends.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            results = run_all_scrapers(session)
            session.commit()
            return results
        except Exception:
            session.rollback()
            raise


@shared_task(name="backend.scraper.runner.scrape_venue")  # type: ignore[untyped-decorator]
def scrape_venue(venue_slug: str) -> dict[str, Any]:
    """Celery task: run the scraper for a single venue.

    Useful as an ops primitive — an admin endpoint or on-call human
    can queue a single-venue re-scrape without triggering the full
    nightly job.

    Args:
        venue_slug: Slug of the venue to scrape.

    Returns:
        The run result dict from :func:`run_scraper_for_venue`.

    Raises:
        ValueError: If no venue config exists for ``venue_slug``, or the
            venue is present but disabled.
    """
    config = get_venue_config(venue_slug)
    if config is None:
        raise ValueError(f"No scraper config for venue '{venue_slug}'")
    if not config.enabled:
        raise ValueError(f"Scraper for venue '{venue_slug}' is disabled.")

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            result = run_scraper_for_venue(session, config)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def run_all_scrapers(session: Session) -> dict[str, Any]:
    """Run scrapers for all enabled venues.

    Iterates through all enabled venue configs, runs each scraper,
    ingests results, and returns a summary.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Dictionary summarizing results per venue.
    """
    configs = get_enabled_configs()
    logger.info("Running scrapers for %d venues.", len(configs))

    results: dict[str, Any] = {}
    for config in configs:
        result = run_scraper_for_venue(session, config)
        results[config.venue_slug] = result

    succeeded = sum(1 for r in results.values() if r["status"] == "success")
    failed = sum(1 for r in results.values() if r["status"] == "failed")
    logger.info(
        "Scraper run complete: %d succeeded, %d failed out of %d.",
        succeeded,
        failed,
        len(configs),
    )

    return results


def run_scraper_for_venue(
    session: Session,
    config: VenueScraperConfig,
) -> dict[str, Any]:
    """Run the scraper for a single venue and ingest results.

    Instantiates the scraper class, collects RawEvents, ingests them
    into the database, logs the run, and validates results.

    Args:
        session: Active SQLAlchemy session.
        config: The venue's scraper configuration.

    Returns:
        Dictionary with run status, event counts, and timing.
    """
    started_at = datetime.now(UTC)
    logger.info("Starting scraper for '%s'.", config.venue_slug)

    try:
        scraper = _instantiate_scraper(config)
        raw_events = list(scraper.scrape())
        event_count = len(raw_events)

        # Ingest into DB
        created, updated, skipped = _ingest_events(
            session,
            config.venue_slug,
            raw_events,
            source_platform=scraper.source_platform,
        )

        finished_at = datetime.now(UTC)
        duration = (finished_at - started_at).total_seconds()

        # Log the run
        runs_repo.create_scraper_run(
            session,
            venue_slug=config.venue_slug,
            scraper_class=config.scraper_class,
            status=ScraperRunStatus.SUCCESS,
            event_count=event_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            metadata_json={
                "created": created,
                "updated": updated,
                "skipped": skipped,
            },
        )

        # Validate
        validate_scraper_result(
            session,
            venue_slug=config.venue_slug,
            event_count=event_count,
        )

        logger.info(
            "Scraper for '%s' completed: %d events "
            "(created=%d, updated=%d, skipped=%d) in %.1fs.",
            config.venue_slug,
            event_count,
            created,
            updated,
            skipped,
            duration,
        )

        return {
            "status": "success",
            "event_count": event_count,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "duration_seconds": duration,
        }

    except Exception as e:
        finished_at = datetime.now(UTC)
        duration = (finished_at - started_at).total_seconds()

        logger.exception("Scraper for '%s' failed: %s", config.venue_slug, e)

        # Log the failed run
        runs_repo.create_scraper_run(
            session,
            venue_slug=config.venue_slug,
            scraper_class=config.scraper_class,
            status=ScraperRunStatus.FAILED,
            event_count=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            error_message=str(e),
        )

        send_alert(
            title=f"Scraper failed: {config.venue_slug}",
            message=f"Scraper for '{config.venue_slug}' raised an exception: {e}",
            severity="error",
            details={
                "venue": config.venue_slug,
                "scraper_class": config.scraper_class,
                "error": str(e),
            },
        )

        return {
            "status": "failed",
            "error": str(e),
            "duration_seconds": duration,
        }


def _instantiate_scraper(config: VenueScraperConfig) -> BaseScraper:
    """Dynamically instantiate a scraper class from its dotted path.

    Args:
        config: The venue's scraper configuration.

    Returns:
        An instance of the scraper class.

    Raises:
        ImportError: If the scraper class cannot be imported.
        TypeError: If the class is not a BaseScraper subclass.
    """
    module_path, class_name = config.scraper_class.rsplit(".", 1)
    module = importlib.import_module(module_path)
    scraper_class = getattr(module, class_name)

    if not issubclass(scraper_class, BaseScraper):
        raise TypeError(f"{config.scraper_class} is not a BaseScraper subclass.")

    scraper: BaseScraper = scraper_class(**config.platform_config)
    return scraper


def _ingest_events(
    session: Session,
    venue_slug: str,
    raw_events: list[RawEvent],
    *,
    source_platform: str,
) -> tuple[int, int, int]:
    """Ingest a list of RawEvents into the database.

    Deduplicates by external_id + source_platform. Creates new events
    or updates existing ones. Skips events that haven't changed.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue these events belong to.
        raw_events: List of RawEvent instances from the scraper.
        source_platform: Identifier of the platform the events came from
            (e.g. ``"ticketmaster"``, ``"generic_html"``, ``"black_cat"``).
            Stored on ``Event.source_platform`` and used together with
            external_id for deduplication.

    Returns:
        Tuple of (created_count, updated_count, skipped_count).
    """
    venue = venues_repo.get_venue_by_slug(session, venue_slug)
    if venue is None:
        logger.error(
            "Venue '%s' not found in database, skipping ingestion.",
            venue_slug,
        )
        return 0, 0, len(raw_events)

    created = 0
    updated = 0
    skipped = 0

    for raw in raw_events:
        external_id = _extract_external_id(raw)
        existing = events_repo.get_event_by_external_id(
            session, external_id, source_platform
        )

        if existing is not None:
            # Update existing event
            changed = _update_event_from_raw(existing, raw)
            if changed:
                session.flush()
                updated += 1
            else:
                skipped += 1
        else:
            # Create new event
            slug = _generate_slug(raw.title, venue_slug, raw.starts_at, external_id)
            events_repo.create_event(
                session,
                venue_id=venue.id,
                title=raw.title,
                slug=slug,
                description=raw.description,
                event_type=EventType.CONCERT,
                status=EventStatus.CONFIRMED,
                starts_at=raw.starts_at,
                ends_at=raw.ends_at,
                on_sale_at=raw.on_sale_at,
                artists=raw.artists,
                image_url=raw.image_url,
                ticket_url=raw.ticket_url,
                min_price=raw.min_price,
                max_price=raw.max_price,
                source_url=raw.source_url,
                raw_data=raw.raw_data,
                external_id=external_id,
                source_platform=source_platform,
            )
            created += 1

    return created, updated, skipped


def _extract_external_id(raw: RawEvent) -> str:
    """Extract a stable external ID for a RawEvent.

    Prefers an explicit ``id`` or ``@id`` field on the raw payload (both
    Ticketmaster and JSON-LD surface one of these). When neither is
    present, derives a deterministic SHA-256 hash from the source URL,
    title, and start time so the same event yields the same external_id
    across runs and can be deduplicated.

    Args:
        raw: The RawEvent instance.

    Returns:
        External ID string. Never None — callers rely on this for
        deduplication and a missing value would create a duplicate
        event on every scrape.
    """
    if raw.raw_data:
        for key in ("id", "@id", "identifier"):
            value = raw.raw_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int | float):
                return str(value)

    fingerprint = f"{raw.source_url}|{raw.title}|{raw.starts_at.isoformat()}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]


def _update_event_from_raw(event: Event, raw: RawEvent) -> bool:
    """Update an existing event with fresh data from a scraper.

    Only updates fields that have actually changed.

    Args:
        event: The existing Event instance.
        raw: The fresh RawEvent data.

    Returns:
        True if any fields were updated, False if unchanged.
    """
    changed = False
    field_map: list[tuple[str, Any]] = [
        ("title", raw.title),
        ("description", raw.description),
        ("starts_at", raw.starts_at),
        ("ends_at", raw.ends_at),
        ("on_sale_at", raw.on_sale_at),
        ("artists", raw.artists),
        ("image_url", raw.image_url),
        ("ticket_url", raw.ticket_url),
        ("min_price", raw.min_price),
        ("max_price", raw.max_price),
        ("source_url", raw.source_url),
        ("raw_data", raw.raw_data),
    ]

    for field_name, new_value in field_map:
        current = getattr(event, field_name)
        if current != new_value and new_value is not None:
            setattr(event, field_name, new_value)
            changed = True

    return changed


def _generate_slug(
    title: str, venue_slug: str, starts_at: datetime, external_id: str
) -> str:
    """Generate a deterministic URL-safe slug for an event.

    The slug must be unique across all events (enforced by a DB
    constraint) AND stable for the same event across re-runs. Earlier
    versions appended ``int(time.time())``, which both (a) collided
    whenever two events in the same venue/title landed in the same
    scrape-second and (b) re-generated different slugs on re-runs
    even when dedup by external_id prevented duplicate rows.

    We now bake the event's start date and a short hash of its
    external_id into the slug so it is deterministic per-event.

    Args:
        title: The event title.
        venue_slug: The venue's slug.
        starts_at: The event's scheduled start (used for the date part
            so two shows with the same title on different nights get
            distinct slugs).
        external_id: The event's external identifier — hashed into a
            short suffix so colliding titles on the same date still
            resolve to unique slugs.

    Returns:
        A URL-safe slug string of the form
        ``<title>-<venue>-YYYY-MM-DD-<6charhash>``.
    """
    text = f"{title} {venue_slug}"
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")

    date_part = starts_at.strftime("%Y-%m-%d")
    suffix = hashlib.sha256(external_id.encode()).hexdigest()[:6]
    return f"{slug}-{date_part}-{suffix}"
