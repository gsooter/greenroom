"""Manual runner for the scraper fleet.

Thin CLI around :func:`backend.scraper.runner.run_all_scrapers` /
:func:`backend.scraper.runner.run_scraper_for_venue`. Intended for
manual ingest, debugging, or one-off backfills — the scheduled
production path is the Celery task that calls the same functions.

Usage:
    python -m backend.scripts.run_scrapers                 # all enabled venues
    python -m backend.scripts.run_scrapers --venue 930-club
    python -m backend.scripts.run_scrapers --dry-run       # scrape, no DB writes
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.scraper.config.venues import get_enabled_configs, get_venue_config
from backend.scraper.runner import (
    _instantiate_scraper,
    run_scraper_for_venue,
)

logger = get_logger(__name__)


def _dry_run(venue_slug: str | None) -> int:
    """Run scrapers in memory and print counts without writing to the DB.

    Args:
        venue_slug: Optional slug to restrict the run to a single venue.

    Returns:
        Exit code — 0 when at least one event was collected across the
        selected venues, 1 when every selected venue returned zero.
    """
    if venue_slug:
        config = get_venue_config(venue_slug)
        if config is None or not config.enabled:
            print(f"Venue '{venue_slug}' not found or not enabled.")
            return 1
        configs = [config]
    else:
        configs = get_enabled_configs()

    print(f"[dry-run] Scraping {len(configs)} venue(s)...\n")
    total = 0
    for config in configs:
        try:
            scraper = _instantiate_scraper(config)
            events = list(scraper.scrape())
        except Exception as exc:
            print(f"  [ERROR] {config.venue_slug}: {exc}")
            continue
        total += len(events)
        marker = "OK  " if events else "ZERO"
        first_line = ""
        if events:
            head = events[0]
            first_line = f"   first: {head.starts_at:%Y-%m-%d %H:%M}  {head.title}"
        print(f"  [{marker}] {config.venue_slug:<32} {len(events):>3} events{first_line}")

    print(f"\nTotal events (not persisted): {total}")
    return 0 if total > 0 else 1


def _live_run(venue_slug: str | None) -> int:
    """Run scrapers and persist events via the real runner.

    Args:
        venue_slug: Optional slug to restrict the run to a single venue.

    Returns:
        Exit code — 0 when every selected scraper succeeded, 1 when
        one or more failed.
    """
    if venue_slug:
        config = get_venue_config(venue_slug)
        if config is None or not config.enabled:
            print(f"Venue '{venue_slug}' not found or not enabled.")
            return 1
        configs = [config]
    else:
        configs = get_enabled_configs()

    print(f"Running {len(configs)} scraper(s) against the database...\n")
    session_factory = get_session_factory()
    failures: list[str] = []

    for config in configs:
        with session_factory() as session:
            try:
                result = run_scraper_for_venue(session, config)
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"  [ERROR] {config.venue_slug}: {exc}")
                failures.append(config.venue_slug)
                continue

        _print_result(config.venue_slug, result)
        if result.get("status") != "success":
            failures.append(config.venue_slug)

    print()
    if failures:
        print(f"Failed venues ({len(failures)}): {', '.join(failures)}")
        return 1
    print("All scrapers completed successfully.")
    return 0


def _print_result(slug: str, result: dict[str, Any]) -> None:
    """Print a single-line summary for one scraper run.

    Args:
        slug: The venue slug.
        result: The dict returned by :func:`run_scraper_for_venue`.
    """
    status = result.get("status", "?")
    if status == "success":
        count = result.get("event_count", 0)
        created = result.get("created", 0)
        updated = result.get("updated", 0)
        skipped = result.get("skipped", 0)
        duration = result.get("duration_seconds", 0.0)
        marker = "OK  " if count > 0 else "ZERO"
        print(
            f"  [{marker}] {slug:<32} "
            f"{count:>3} events   "
            f"(+{created} ~{updated} ={skipped})   "
            f"{duration:.1f}s"
        )
    else:
        print(f"  [FAIL] {slug:<32} {result.get('error', 'unknown error')}")


def main() -> int:
    """Parse arguments and dispatch to the chosen run mode.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venue",
        help="Run only this venue slug (otherwise runs every enabled venue).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and report counts, but do not write to the database.",
    )
    args = parser.parse_args()

    if args.dry_run:
        return _dry_run(args.venue)
    return _live_run(args.venue)


if __name__ == "__main__":
    sys.exit(main())
