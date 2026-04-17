"""Live smoke probe for Ticketmaster venue IDs.

Runs :class:`TicketmasterScraper` against every Ticketmaster-backed
venue in ``backend.scraper.config.venues`` and prints a one-line
summary per venue. No database writes happen — this is strictly a
sanity check that every hard-coded ``venue_id`` is still valid and
still returns events from the Discovery API.

Safe to run manually whenever you suspect a venue ID has drifted or
the TM schema has changed. Burns one API call per page per venue, so
roughly 12–24 calls against the daily quota.

Usage:
    python -m backend.scripts.smoke_ticketmaster
    python -m backend.scripts.smoke_ticketmaster --venue 930-club
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.core.config import get_settings
from backend.scraper.config.venues import VENUE_CONFIGS, VenueScraperConfig
from backend.scraper.platforms.ticketmaster import TicketmasterScraper


_TM_CLASS_PATH = "backend.scraper.platforms.ticketmaster.TicketmasterScraper"


def _ticketmaster_configs() -> list[VenueScraperConfig]:
    """Return every enabled Ticketmaster-backed venue config.

    Returns:
        List of configs whose ``scraper_class`` points at the TM platform.
    """
    return [
        c
        for c in VENUE_CONFIGS
        if c.scraper_class == _TM_CLASS_PATH and c.enabled
    ]


def _probe_venue(
    config: VenueScraperConfig, *, api_key: str
) -> tuple[str, int, str | None]:
    """Run the Ticketmaster scraper for one venue and collect results.

    Args:
        config: The venue scraper config to probe.
        api_key: Ticketmaster Discovery API key.

    Returns:
        Tuple of (venue_slug, event_count, first_event_summary).
        ``first_event_summary`` is None when the venue returns no events.
    """
    scraper = TicketmasterScraper(
        venue_id=config.platform_config["venue_id"],
        venue_name=config.platform_config["venue_name"],
        api_key=api_key,
    )
    events = list(scraper.scrape())
    first: str | None = None
    if events:
        head = events[0]
        first = f"{head.starts_at:%Y-%m-%d %H:%M}  {head.title}"
    return config.venue_slug, len(events), first


def main() -> int:
    """Run the smoke probe and print a summary.

    Returns:
        Exit code: 0 on success, 1 if any venue returns zero events.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venue",
        help="Probe only this venue slug (otherwise probes all TM venues).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full scraper retry/error logs (default suppresses them).",
    )
    args = parser.parse_args()

    if not args.verbose:
        # Suppress the scraper module's per-retry ERROR logs; the summary
        # already surfaces zero-event venues and exceptions.
        logging.getLogger(
            "backend.scraper.platforms.ticketmaster"
        ).setLevel(logging.CRITICAL)

    api_key = get_settings().ticketmaster_api_key
    if api_key in {"", "dev-placeholder"}:
        print(
            "TICKETMASTER_API_KEY is unset or a placeholder in .env — "
            "register a Discovery API key at "
            "https://developer.ticketmaster.com/ and set it before running.",
            file=sys.stderr,
        )
        return 1
    configs = _ticketmaster_configs()
    if args.venue:
        configs = [c for c in configs if c.venue_slug == args.venue]
        if not configs:
            print(f"No Ticketmaster venue found with slug '{args.venue}'.")
            return 1

    print(f"Probing {len(configs)} Ticketmaster venue(s)...\n")

    empty_venues: list[str] = []
    for config in configs:
        try:
            slug, count, first = _probe_venue(config, api_key=api_key)
        except Exception as exc:
            print(f"  [ERROR] {config.venue_slug}: {exc}")
            empty_venues.append(config.venue_slug)
            continue

        marker = "OK  " if count > 0 else "ZERO"
        summary = f"  [{marker}] {slug:<32} {count:>3} events"
        if first:
            summary += f"   first: {first}"
        print(summary)
        if count == 0:
            empty_venues.append(slug)

    print()
    if empty_venues:
        print(
            "Venues returning zero events or errors "
            f"({len(empty_venues)}): {', '.join(empty_venues)}"
        )
        return 1
    print("All Ticketmaster venues returned at least one event.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
