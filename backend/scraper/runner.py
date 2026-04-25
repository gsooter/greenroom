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
from zoneinfo import ZoneInfo

from celery import shared_task
from sqlalchemy.orm import Session

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.models.scraper import ScraperRunStatus
from backend.data.repositories import artists as artists_repo
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


FLEET_FAILURE_THRESHOLD = 0.4
"""Fraction of venues that must fail in one batch to fire a fleet alert.

Per-venue alerts already deduplicate, but a mass failure (DB outage,
expired credentials, blocked IP) would still emit N separate alerts in
quick succession before any cooldown kicks in. The fleet alert is a
single, distinct signal that lets the operator see "the scraper run
itself broke, not one venue."
"""

ESCALATION_FAILURE_THRESHOLD = 3
"""Consecutive failures that flip a venue from "flake" to "sustained outage."

The first failure already fires a per-venue ``scraper_failed:`` alert.
Three in a row means the venue has been broken across multiple nightly
runs and the operator should treat it as a real fix-it task rather
than a transient blip.
"""


def run_all_scrapers(session: Session) -> dict[str, Any]:
    """Run scrapers for all enabled venues.

    Iterates through all enabled venue configs, runs each scraper,
    ingests results, and returns a summary. Fires a fleet-wide alert
    when the failure rate exceeds :data:`FLEET_FAILURE_THRESHOLD`.

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

    _maybe_alert_fleet_failure(
        session=session,
        total=len(configs),
        failed=failed,
        results=results,
    )

    return results


def _maybe_alert_fleet_failure(
    *,
    session: Session,
    total: int,
    failed: int,
    results: dict[str, Any],
) -> None:
    """Emit a single fleet-wide alert when too many scrapers failed at once.

    A nightly run with most venues failing usually points at infrastructure
    (database, Redis, outbound network, expired API keys) rather than at
    any one venue. Surfacing it as one signal — rather than letting the
    per-venue alerts pile up in Slack — keeps the on-call channel readable.

    Args:
        session: Active SQLAlchemy session, reused for the alert dedup row.
        total: Total number of venues that ran.
        failed: Number of venues whose run ended in ``status == "failed"``.
        results: The per-venue results dict, used to enumerate which
            venues are in the failed set.
    """
    if total == 0 or failed == 0:
        return
    failure_rate = failed / total
    if failure_rate < FLEET_FAILURE_THRESHOLD:
        return

    failed_venues = sorted(
        slug for slug, info in results.items() if info["status"] == "failed"
    )
    preview = ", ".join(failed_venues[:10])
    if len(failed_venues) > 10:
        preview += f", ...(+{len(failed_venues) - 10} more)"

    send_alert(
        title=(f"Fleet failure: {failed}/{total} scrapers failed ({failure_rate:.0%})"),
        message=(
            f"{failed} of {total} enabled scrapers ({failure_rate:.0%}) failed "
            f"in the same batch. This usually means a shared dependency is "
            f"broken (DB, Redis, outbound network, credentials). "
            f"Failed venues: {preview}."
        ),
        severity="error",
        details={
            "total": total,
            "failed": failed,
            "failure_rate": f"{failure_rate:.0%}",
            "venues": preview,
        },
        alert_key="fleet_failure",
        cooldown_hours=2.0,
        session=session,
    )


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
            alert_key=f"scraper_failed:{config.venue_slug}",
            cooldown_hours=6.0,
            session=session,
        )

        _maybe_alert_escalation(
            session=session,
            venue_slug=config.venue_slug,
            scraper_class=config.scraper_class,
            last_error=str(e),
        )

        return {
            "status": "failed",
            "error": str(e),
            "duration_seconds": duration,
        }


def _maybe_alert_escalation(
    *,
    session: Session,
    venue_slug: str,
    scraper_class: str,
    last_error: str,
) -> None:
    """Fire a separate escalation alert when consecutive failures pile up.

    The runner has just inserted a fresh ``FAILED`` row, so the head of
    the run history reflects the current outage. If the last
    :data:`ESCALATION_FAILURE_THRESHOLD` runs are all FAILED, the venue
    is in a sustained-outage state and deserves an attention-grabbing
    alert distinct from the per-run "scraper failed" notice.

    The escalation alert key is venue-scoped and uses a 24h cooldown
    so the operator gets a daily reminder until they intervene — but
    Slack isn't carpet-bombed.

    Args:
        session: Active SQLAlchemy session, reused for both the count
            query and the alert dedup row.
        venue_slug: Slug of the venue that just failed.
        scraper_class: Fully qualified scraper class for context.
        last_error: Stringified exception from the most recent failure.
    """
    consecutive = runs_repo.count_consecutive_failed_runs(session, venue_slug)
    if consecutive < ESCALATION_FAILURE_THRESHOLD:
        return
    send_alert(
        title=f"Sustained outage: {venue_slug} ({consecutive} consecutive failures)",
        message=(
            f"Scraper for '{venue_slug}' has now failed {consecutive} runs "
            f"in a row. This is no longer a transient flake — the integration "
            f"likely needs a fix. Most recent error: {last_error}"
        ),
        severity="error",
        details={
            "venue": venue_slug,
            "scraper_class": scraper_class,
            "consecutive_failures": consecutive,
            "last_error": last_error,
        },
        alert_key=f"escalation:{venue_slug}",
        cooldown_hours=24.0,
        session=session,
    )


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

    # Every scraper yields naive wall-clock datetimes in the venue's local
    # zone; we localize here so events.starts_at (timestamptz) is stored
    # in UTC with the correct offset applied.
    venue_tz = venue.city.timezone

    created = 0
    updated = 0
    skipped = 0

    for raw in raw_events:
        _upsert_artists(session, raw.artists)
        external_id = _extract_external_id(raw)
        existing = events_repo.get_event_by_external_id(
            session, external_id, source_platform
        )

        if existing is not None:
            # Update existing event
            changed = _update_event_from_raw(existing, raw, venue_tz)
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
                starts_at=_localize_venue_datetime(raw.starts_at, venue_tz),
                ends_at=_localize_venue_datetime(raw.ends_at, venue_tz),
                on_sale_at=_localize_venue_datetime(raw.on_sale_at, venue_tz),
                artists=raw.artists,
                genres=raw.genres or None,
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


def _upsert_artists(session: Session, names: list[str]) -> None:
    """Upsert each performer name onto the artists table.

    Called once per RawEvent during ingestion so the ``artists`` table
    grows as events come in. The Spotify enrichment Celery task picks
    up from there — no synchronous Spotify calls in the scraper path.

    Args:
        session: Active SQLAlchemy session.
        names: Performer names straight off the RawEvent.
    """
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        artists_repo.upsert_artist_by_name(session, name)


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


def _localize_venue_datetime(value: datetime | None, tz_name: str) -> datetime | None:
    """Treat a naive scraper datetime as venue-local wall time and return UTC.

    Scrapers yield naive datetimes whose fields reflect the venue's local
    wall clock — a 7 pm show at Black Cat is ``datetime(y, m, d, 19, 0)``
    with no tzinfo. The ``events.starts_at`` column is ``timestamptz``,
    so a naive value gets silently stored as UTC, shifting every DMV show
    four-to-five hours earlier than it actually plays. Attaching the
    venue's IANA zone before converting to UTC is the fix.

    Datetimes that already carry tzinfo are normalized to UTC but not
    re-localized, so future tz-aware scrapers aren't double-converted.

    Args:
        value: The raw datetime from a RawEvent, possibly naive or None.
        tz_name: IANA timezone name, e.g. ``"America/New_York"``.

    Returns:
        A timezone-aware datetime in UTC, or None when ``value`` is None.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def _update_event_from_raw(event: Event, raw: RawEvent, venue_tz: str) -> bool:
    """Update an existing event with fresh data from a scraper.

    Only updates fields that have actually changed. Datetime fields are
    localized from the venue's timezone to UTC before comparison so a
    re-scrape of the same naive wall-clock time doesn't appear as a diff.

    Args:
        event: The existing Event instance.
        raw: The fresh RawEvent data.
        venue_tz: IANA timezone name for the venue's city.

    Returns:
        True if any fields were updated, False if unchanged.
    """
    changed = False
    field_map: list[tuple[str, Any]] = [
        ("title", raw.title),
        ("description", raw.description),
        ("starts_at", _localize_venue_datetime(raw.starts_at, venue_tz)),
        ("ends_at", _localize_venue_datetime(raw.ends_at, venue_tz)),
        ("on_sale_at", _localize_venue_datetime(raw.on_sale_at, venue_tz)),
        ("artists", raw.artists),
        ("genres", raw.genres or None),
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
