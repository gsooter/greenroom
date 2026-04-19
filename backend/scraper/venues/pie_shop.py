"""Custom scraper for Pie Shop (Washington, DC).

Pie Shop's public events page at https://www.pieshopdc.com/shows renders
a Webflow collection list. The page does not emit JSON-LD, but every
show is a ``.uui-layout88_item.w-dyn-item`` block containing the title,
month, day, time, image, detail URL, and an optional status tag image
(e.g. "Sold out") that we map onto ``raw_data``.

The year is not published anywhere on the page, so we rely on
:mod:`backend.scraper.base.dates` to roll dates forward into next year
once today's reference date has passed them.
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

PIE_SHOP_URL = "https://www.pieshopdc.com/shows"
VENUE_EXTERNAL_ID = "pie-shop"


class PieShopScraper(BaseScraper):
    """Scrape upcoming shows from pieshopdc.com/shows.

    The page is a Webflow collection. Each show block contains the
    title in an ``h3``, a date sticker with ``.event-month`` /
    ``.event-day`` / ``.event-time-new`` elements, and a status image
    (``.event-tag``) that is visually hidden via the Webflow
    ``w-condition-invisible`` class when no status applies.

    Attributes:
        url: Pie Shop's shows page.
        today: Optional reference date used for year inference in tests.
    """

    source_platform = "pie_shop"

    def __init__(
        self,
        *,
        url: str = PIE_SHOP_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Pie Shop scraper.

        Args:
            url: Override for the shows page URL.
            today: Optional reference date used for year inference.
                Tests pass a fixed value; production scrapes use
                ``date.today()`` implicitly.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the Pie Shop shows page and yield one RawEvent per show.

        Yields:
            RawEvent instances for every event block on the page.
        """
        logger.info("PieShopScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse the Webflow collection markup into RawEvents.

        Args:
            html: The fully-fetched shows page HTML.

        Yields:
            RawEvent for every Webflow ``.w-dyn-item`` block that has
            the minimum required title + parseable date.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".uui-layout88_item.w-dyn-item")
        logger.info("PieShopScraper found %d show blocks.", len(items))

        count = 0
        for item in items:
            event = self._item_to_raw_event(item)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "PieShopScraper parsed no events on %s — "
                "Webflow markup may have changed.",
                self.url,
            )
        else:
            logger.info("PieShopScraper yielded %d events.", count)

    def _item_to_raw_event(self, item: Tag) -> RawEvent | None:
        """Convert one ``.uui-layout88_item`` block into a ``RawEvent``.

        Args:
            item: The BeautifulSoup tag for a single show container.

        Returns:
            A populated RawEvent, or None when the block lacks enough
            data to produce one (missing title or unparseable date).
        """
        title_el = item.select_one("h3, .uui-heading-xxsmall-2")
        title = _text(title_el)
        if not title:
            return None

        month_el = item.select_one(".event-month")
        day_el = item.select_one(".event-day")
        time_el = item.select_one(".event-time-new")

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

        image_url = _visible_image(item)

        status = _event_tag_status(item)

        raw_data: dict[str, Any] = {
            "title": title,
            "event_month": _text(month_el),
            "event_day": day_text,
            "event_time": _text(time_el),
            "status": status,
            "source": "pie_shop_html",
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
        The stripped text content, or None when absent or empty.
    """
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return text or None


def _visible_image(item: Tag) -> str | None:
    """Return the URL of the show image that Webflow renders (not the hidden copy).

    Pie Shop renders both a hidden ``w-condition-invisible`` copy and a
    visible one. We want the latter.

    Args:
        item: The show's container element.

    Returns:
        Absolute image URL, or None when no usable image is present.
    """
    for img in item.select(".show-image-wrapper img[src]"):
        classes = img.get("class") or []
        if "w-condition-invisible" in classes:
            continue
        src = img.get("src")
        if isinstance(src, str) and src.strip():
            return src.strip()
    first = item.select_one("img[src]")
    if isinstance(first, Tag):
        src = first.get("src")
        if isinstance(src, str) and src.strip():
            return src.strip()
    return None


def _event_tag_status(item: Tag) -> str | None:
    """Detect the status label (``sold out``, ``postponed``, etc.) on a block.

    Pie Shop renders the status as an ``.event-tag`` image. When no
    status applies the image carries the ``w-condition-invisible``
    class. We only treat the tag as real when it is visible.

    Args:
        item: The show's container element.

    Returns:
        Lowercase status string (e.g. ``"sold out"``) when a visible
        tag exists, otherwise None.
    """
    tag = item.select_one(".event-tag")
    if not isinstance(tag, Tag):
        return None
    classes = tag.get("class") or []
    if "w-condition-invisible" in classes:
        return None
    src = tag.get("src") or ""
    alt = tag.get("alt") or ""
    if isinstance(alt, str) and alt.strip() and alt.lower() != "event status indicator":
        return alt.strip().lower()
    if isinstance(src, str) and src:
        filename = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        filename = filename.replace("%20", " ").replace("_", " ")
        return filename.strip().lower() or None
    return None
