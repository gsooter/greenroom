"""Custom scraper for Comet Ping Pong (Washington, DC).

Comet embeds its calendar via a Firebooking iframe pointed at
``https://calendar.rediscoverfirebooking.com/cpp-shows-preview``. The
iframe is a Webflow page that renders one ``.uui-layout88_item-cpp``
block per show with the full date string ("April 17, 2026") and show
time. The Comet homepage itself publishes only ``WebSite`` /
``LocalBusiness`` JSON-LD with no events.

We scrape the iframe directly — it is a public URL — and attribute
events back to ``www.cometpingpong.com`` in ``source_url`` so
customer-facing links stay on the public site.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from backend.core.logging import get_logger
from backend.scraper.base.dates import parse_clock_time
from backend.scraper.base.http import fetch_html
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = get_logger(__name__)

COMET_CALENDAR_URL = "https://calendar.rediscoverfirebooking.com/cpp-shows-preview"
COMET_PUBLIC_URL = "https://www.cometpingpong.com/"
VENUE_EXTERNAL_ID = "comet-ping-pong"


class CometPingPongScraper(BaseScraper):
    """Scrape Comet Ping Pong shows from the Firebooking iframe page.

    Attributes:
        calendar_url: URL of the embedded Firebooking calendar.
        public_url: Public-facing Comet URL used as the ``source_url``
            base when the calendar does not carry an absolute link.
    """

    source_platform = "comet_ping_pong"

    def __init__(
        self,
        *,
        calendar_url: str = COMET_CALENDAR_URL,
        public_url: str = COMET_PUBLIC_URL,
    ) -> None:
        """Configure the Comet Ping Pong scraper.

        Args:
            calendar_url: Firebooking iframe URL to fetch.
            public_url: Public Comet URL used for branding/fallback links.
        """
        self.calendar_url = calendar_url
        self.public_url = public_url

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the Firebooking calendar and yield one RawEvent per show.

        Yields:
            RawEvent instances for every show block in the iframe.
        """
        logger.info("CometPingPongScraper fetching %s", self.calendar_url)
        html = fetch_html(self.calendar_url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse Firebooking's Webflow markup into RawEvents.

        Args:
            html: The fully-fetched calendar HTML.

        Yields:
            RawEvent for every parseable ``.uui-layout88_item-cpp`` block.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".uui-layout88_item-cpp.w-dyn-item")
        logger.info("CometPingPongScraper found %d show blocks.", len(items))

        count = 0
        for item in items:
            event = self._item_to_raw_event(item)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "CometPingPongScraper parsed no events on %s — "
                "Firebooking markup may have changed.",
                self.calendar_url,
            )
        else:
            logger.info("CometPingPongScraper yielded %d events.", count)

    def _item_to_raw_event(self, item: Tag) -> RawEvent | None:
        """Convert a single Firebooking show block into a ``RawEvent``.

        Args:
            item: The Webflow ``.uui-layout88_item-cpp`` element.

        Returns:
            A populated RawEvent, or None when the block is missing
            required fields (title or parseable date).
        """
        title = _text(item.select_one("h3, .uui-heading-xxsmall-2"))
        if not title:
            return None

        date_text = _text(item.select_one(".heading-date"))
        time_text = _text(item.select_one(".heading-time"))
        starts_at = _parse_long_date(date_text, time_text)
        if starts_at is None:
            return None

        ticket_url: str | None = None
        for selector in ("a.link-block-2[href]", "a.link-block-4[href]", "a[href]"):
            link = item.select_one(selector)
            if isinstance(link, Tag):
                href = link.get("href")
                if isinstance(href, str) and href.strip():
                    ticket_url = urljoin(self.calendar_url, href.strip())
                    break

        image_url: str | None = None
        img = item.select_one("img.image-42[src], img[src]")
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = src.strip()

        ages = _text(item.select_one(".ages-2"))
        status = _event_tag_status(item)

        raw_data: dict[str, Any] = {
            "title": title,
            "heading_date": date_text,
            "heading_time": time_text,
            "ages": ages,
            "status": status,
            "source": "comet_firebooking_html",
        }

        artists = [a.strip() for a in re.split(r",|\band\b", title) if a.strip()]

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=ticket_url or self.public_url,
            raw_data=raw_data,
            artists=artists or [title],
            ticket_url=ticket_url,
            image_url=image_url,
        )


def _text(element: Tag | None) -> str | None:
    """Return trimmed text of an element, or None when absent/blank.

    Args:
        element: BeautifulSoup tag or None.

    Returns:
        Stripped text content, or None.
    """
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return text or None


_LONG_DATE_FORMATS: tuple[str, ...] = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
)


def _parse_long_date(date_text: str | None, time_text: str | None) -> datetime | None:
    """Combine a ``heading-date`` string like "April 17, 2026" with the show time.

    Args:
        date_text: The raw date string from ``.heading-date``.
        time_text: The raw time string from ``.heading-time``.

    Returns:
        A ``datetime`` in venue-local time when the date parses,
        otherwise None. Missing times default to 8:00 PM.
    """
    if not date_text:
        return None
    cleaned = re.sub(r"\s+", " ", date_text).strip()
    parsed: datetime | None = None
    for pattern in _LONG_DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        return None

    clock = parse_clock_time(time_text)
    hour, minute = clock if clock is not None else (20, 0)
    return parsed.replace(hour=hour, minute=minute)


def _event_tag_status(item: Tag) -> str | None:
    """Detect the status label ("sold out", etc.) on a Firebooking show block.

    Firebooking renders status as an ``.event-tag`` image. When no
    status applies the image is hidden via ``w-condition-invisible``.

    Args:
        item: The show's container element.

    Returns:
        Lowercase status string, or None when no visible tag exists.
    """
    tag = item.select_one(".event-tag")
    if not isinstance(tag, Tag):
        return None
    classes = tag.get("class") or []
    if "w-condition-invisible" in classes:
        return None
    alt = tag.get("alt")
    if isinstance(alt, str) and alt.strip():
        return alt.strip().lower()
    return None
