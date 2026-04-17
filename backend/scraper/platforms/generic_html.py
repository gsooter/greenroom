"""Generic HTML scraper for venues that publish schema.org structured data.

Most modern venue sites emit ``<script type="application/ld+json">``
blocks with ``MusicEvent`` or ``Event`` nodes so Google can generate
rich results. When a site does, we prefer that data over CSS
selectors — it survives redesigns and is a well-documented format.

If a specific venue does not publish JSON-LD, drop in a custom scraper
under ``backend/scraper/venues/<slug>.py`` that reuses the HTTP helper
from :mod:`backend.scraper.base.http` and parses whatever structure
that venue does expose. This scraper is intentionally narrow: JSON-LD
or nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

from backend.core.logging import get_logger
from backend.scraper.base.http import fetch_html
from backend.scraper.base.jsonld import extract_events
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

logger = get_logger(__name__)


class GenericHtmlScraper(BaseScraper):
    """Scrape events from a venue page that publishes schema.org JSON-LD.

    The scraper fetches the configured URL, extracts every Event-shaped
    JSON-LD node it can find, and yields a ``RawEvent`` per node. It
    does not attempt heuristic CSS parsing; if a venue has no JSON-LD,
    that is a signal to add a dedicated custom scraper rather than
    fight flaky selectors here.

    Attributes:
        url: Fully qualified URL of the venue's events listing page.
        venue_external_id: Stable identifier stored on each RawEvent.
            Defaults to ``url`` which is a reasonable, stable key for
            JSON-LD-sourced venues.
    """

    source_platform = "generic_html"

    def __init__(
        self,
        *,
        url: str,
        venue_external_id: str | None = None,
    ) -> None:
        """Configure the scraper for a single venue URL.

        Args:
            url: Fully qualified URL of the venue's events listing page.
            venue_external_id: Optional override for the venue identifier
                stored on each scraped ``RawEvent``. Defaults to ``url``.
        """
        self.url = url
        self.venue_external_id = venue_external_id or url

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the configured URL and yield one RawEvent per JSON-LD event.

        Yields:
            RawEvent instances parsed from the page's JSON-LD blocks.
        """
        logger.info("GenericHtmlScraper fetching %s", self.url)
        html = fetch_html(self.url)

        count = 0
        for event in extract_events(
            html,
            source_url=self.url,
            venue_external_id=self.venue_external_id,
        ):
            count += 1
            yield event

        if count == 0:
            logger.warning(
                "GenericHtmlScraper found no JSON-LD events on %s. "
                "If this site never publishes structured data, add a "
                "custom scraper under backend/scraper/venues/ for it.",
                self.url,
            )
        else:
            logger.info(
                "GenericHtmlScraper extracted %d events from %s.", count, self.url
            )
