"""Custom scraper for The Camel (Richmond, VA).

The Camel publishes its calendar at https://www.thecamel.org/shows on a
Webflow site that uses the same ``uui-layout88_item`` collection
template as Pie Shop in DC. Each show is a ``.uui-layout88_item.w-dyn-item``
block carrying a date sticker (``.event-month`` / ``.event-day`` /
``.event-time-new``), an ``h3`` headline, an image, a relative
``/shows/<slug>`` link, and an optional ``.event-tag`` sold-out badge
that Webflow visually hides via ``w-condition-invisible`` when not
applicable.

The page omits a year on the date sticker, so we rely on
:mod:`backend.scraper.base.dates` to roll dates forward into next
year once today's reference date has passed them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from backend.core.logging import get_logger
from backend.scraper.base.dates import (
    build_event_datetime,
    parse_clock_time,
    parse_month_name,
)
from backend.scraper.base.http import fetch_html
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date

logger = get_logger(__name__)

THE_CAMEL_URL = "https://www.thecamel.org/shows"
VENUE_EXTERNAL_ID = "the-camel"


class TheCamelScraper(BaseScraper):
    """Scrape upcoming shows from thecamel.org/shows.

    Attributes:
        url: The /shows page URL.
        today: Optional reference date used for year inference in tests.
    """

    source_platform = "the_camel"

    def __init__(
        self,
        *,
        url: str = THE_CAMEL_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Camel scraper.

        Args:
            url: Override for the shows page URL.
            today: Optional reference date used for year inference.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the shows page and yield one RawEvent per show card.

        Yields:
            RawEvent for every parseable Webflow ``.uui-layout88_item`` block.
        """
        logger.info("TheCamelScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse the Webflow collection markup into RawEvents.

        Args:
            html: The fully-fetched page HTML.

        Yields:
            RawEvent for every block carrying the minimum title +
            parseable date.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".uui-layout88_item.w-dyn-item")
        logger.info("TheCamelScraper found %d show blocks.", len(items))

        count = 0
        for item in items:
            event = self._item_to_raw_event(item)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "TheCamelScraper parsed no events on %s — "
                "Webflow markup may have changed.",
                self.url,
            )
        else:
            logger.info("TheCamelScraper yielded %d events.", count)

    def _item_to_raw_event(self, item: Tag) -> RawEvent | None:
        """Convert one ``.uui-layout88_item`` block into a ``RawEvent``.

        Args:
            item: BeautifulSoup tag wrapping a single show card.

        Returns:
            A populated RawEvent, or None when the card lacks a usable
            title or date.
        """
        title_el = item.select_one("h3.uui-heading-xxsmall-2, h3")
        title = _text(title_el)
        if not title:
            return None

        # Read the visible date sticker (the multi-day variant is
        # toggled with w-condition-invisible when only one day applies).
        sticker = _visible_date_sticker(item)
        if sticker is None:
            return None

        month_el = sticker.select_one(".event-month")
        day_el = sticker.select_one(".event-day")
        time_el = sticker.select_one(".event-time-new")

        month = parse_month_name(_text(month_el) or "")
        day_text = _text(day_el) or ""
        try:
            day = int(day_text)
        except ValueError:
            day = 0
        if month is None or not (1 <= day <= 31):
            return None

        clock = parse_clock_time(_text(time_el))
        hour, minute = clock if clock is not None else (20, 0)
        starts_at = build_event_datetime(
            month=month, day=day, hour=hour, minute=minute, today=self.today
        )

        ticket_url: str | None = None
        link = item.select_one("a.link-block-2[href], a[href]")
        if isinstance(link, Tag):
            href = link.get("href")
            if isinstance(href, str) and href.strip():
                ticket_url = urljoin(self.url, href.strip())

        image_url: str | None = None
        for img in item.select(".show-image-wrapper img[src]"):
            classes = img.get("class") or []
            if "w-condition-invisible" in classes:
                continue
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = src.strip()
                break

        status = _event_tag_status(item)

        raw_data: dict[str, Any] = {
            "title": title,
            "event_month": _text(month_el),
            "event_day": day_text,
            "event_time": _text(time_el),
            "status": status,
            "source": "the_camel_html",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=ticket_url or self.url,
            raw_data=raw_data,
            artists=[title],
            ticket_url=ticket_url,
            image_url=image_url,
        )


def _text(element: Tag | None) -> str | None:
    """Return the trimmed text of an element, or None when blank/missing.

    Args:
        element: BeautifulSoup tag or None.

    Returns:
        Stripped text content, or None when absent or empty.
    """
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return text or None


def _visible_date_sticker(item: Tag) -> Tag | None:
    """Return the visible ``.date-sticker`` element on the card.

    The Camel renders both ``.date-sticker`` (single-day) and
    ``.date-sticker-multi-day`` siblings; the inactive one carries
    ``w-condition-invisible``. Multi-day rows are rare in practice,
    so we only consume the single-day sticker for now.

    Args:
        item: The card's container element.

    Returns:
        The visible date-sticker Tag, or None when none is visible.
    """
    sticker = item.select_one(".date-sticker:not(.w-condition-invisible)")
    if isinstance(sticker, Tag):
        return sticker
    return item.select_one(".date-sticker")


def _event_tag_status(item: Tag) -> str | None:
    """Detect the visible status badge (``sold out``, ``last call``).

    Args:
        item: The card's container element.

    Returns:
        Lowercase status string when a visible ``.event-tag`` exists,
        otherwise None.
    """
    tag = item.select_one(".event-tag")
    if not isinstance(tag, Tag):
        return None
    classes = tag.get("class") or []
    if "w-condition-invisible" in classes:
        return None
    alt = tag.get("alt") or ""
    if isinstance(alt, str) and alt.strip() and alt.lower() != "event status indicator":
        return alt.strip().lower()
    src = tag.get("src") or ""
    if isinstance(src, str) and src:
        filename = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        filename = filename.replace("%20", " ").replace("_", " ")
        return filename.strip().lower() or None
    return None
