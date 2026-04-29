"""Custom scraper for The Ottobar (Baltimore, MD).

The Ottobar publishes its calendar at https://theottobar.com/calendar
on a WordPress site running the Rock Paper Scissors (RHP) events
plugin. Each show is a ``.eventWrapper.rhpSingleEvent`` block carrying
a date sticker (``Sat, Apr 25``), an ``h2.rhp-event__title--list``
headline, a doors-time line (``Doors: 6 pm``), an event-page link, an
optional ``span.rhp-event-cta`` with a class like ``on-sale`` or
``sold-out`` whose inner ``<a href>`` points at the ticket vendor
(typically etix.com), and an image.

The per-event date sticker omits the year, but the page emits month
separators (``.rhp-events-list-separator-month``) like ``April 2026``
between event blocks. The parser walks the list in document order and
treats the most recent separator's year as the year for any subsequent
events. When an event appears before any separator (defensively rare),
we fall back to :mod:`backend.scraper.base.dates` year inference.
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
    from datetime import date, datetime

logger = get_logger(__name__)

OTTOBAR_URL = "https://theottobar.com/calendar/"
VENUE_EXTERNAL_ID = "ottobar"


class OttobarScraper(BaseScraper):
    """Scrape upcoming shows from theottobar.com/calendar.

    The Ottobar's calendar is rendered server-side as a flat list of
    RHP-plugin event blocks interleaved with month separators carrying
    the year. The parser walks both in order so each event picks up the
    year of its preceding separator.

    Attributes:
        url: The calendar page URL.
        today: Optional reference date used for fallback year inference.
    """

    source_platform = "ottobar"

    def __init__(
        self,
        *,
        url: str = OTTOBAR_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Ottobar scraper.

        Args:
            url: Override for the calendar page URL.
            today: Optional reference date used as a fallback for year
                inference when an event block appears before any month
                separator on the page.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the calendar page and yield one RawEvent per show block.

        Yields:
            RawEvent instances for every parseable
            ``.eventWrapper.rhpSingleEvent`` block on the page.
        """
        logger.info("OttobarScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse the RHP-plugin calendar markup into RawEvents.

        Args:
            html: The fully-fetched page HTML.

        Yields:
            RawEvent for every event block carrying the minimum title +
            parseable month/day. The page emits all upcoming events in
            a single rendering — pagination links exist in the DOM but
            they re-render the same list, so we only fetch page 1.
        """
        soup = BeautifulSoup(html, "html.parser")
        nodes = soup.select(
            ".rhp-events-list-separator-month, .eventWrapper.rhpSingleEvent"
        )
        logger.info(
            "OttobarScraper found %d separator/event nodes on the page.",
            len(nodes),
        )

        current_year: int | None = None
        count = 0
        for node in nodes:
            classes = node.get("class") or []
            if "rhp-events-list-separator-month" in classes:
                year = _separator_year(node)
                if year is not None:
                    current_year = year
                continue
            event = self._item_to_raw_event(node, current_year)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "OttobarScraper parsed no events on %s — RHP markup may have changed.",
                self.url,
            )
        else:
            logger.info("OttobarScraper yielded %d events.", count)

    def _item_to_raw_event(
        self, item: Tag, current_year: int | None
    ) -> RawEvent | None:
        """Convert one ``.eventWrapper.rhpSingleEvent`` block into a RawEvent.

        Args:
            item: BeautifulSoup tag wrapping a single show row.
            current_year: Year carried in from the most recent
                ``.rhp-events-list-separator-month`` we walked past, or
                None when no separator has been seen yet.

        Returns:
            A populated RawEvent, or None when the row lacks a usable
            title or month/day pair.
        """
        title_el = item.select_one("h2.rhp-event__title--list")
        title = _text(title_el)
        if not title:
            return None

        date_el = item.select_one(".eventDateList .singleEventDate")
        date_text = _text(date_el) or ""
        month, day = _parse_short_date(date_text)
        if month is None or day is None:
            return None

        time_el = item.select_one(".rhp-event__time-text--list")
        time_text = _text(time_el)
        clock = parse_clock_time(time_text)
        hour, minute = clock if clock is not None else (20, 0)

        starts_at = _build_starts_at(
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            year=current_year,
            today=self.today,
        )

        source_url = self._extract_source_url(item)
        ticket_url, status = _extract_cta(item)
        image_url = _extract_image(item)

        raw_data: dict[str, Any] = {
            "title": title,
            "event_date": date_text,
            "event_time": time_text,
            "status": status,
            "source": "ottobar_html",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=source_url or self.url,
            raw_data=raw_data,
            artists=[title],
            ticket_url=ticket_url or source_url,
            image_url=image_url,
        )

    def _extract_source_url(self, item: Tag) -> str | None:
        """Return the absolute URL of the Ottobar event detail page.

        Args:
            item: The card's container element.

        Returns:
            The absolute event-page URL, or None when no link is found.
        """
        link = item.select_one("a#eventTitle.url[href], a.url[href]")
        if not isinstance(link, Tag):
            return None
        href = link.get("href")
        if not isinstance(href, str) or not href.strip():
            return None
        return urljoin(self.url, href.strip())


def _build_starts_at(
    *,
    month: int,
    day: int,
    hour: int,
    minute: int,
    year: int | None,
    today: date | None,
) -> datetime:
    """Combine month/day/time with an explicit or inferred year.

    Args:
        month: 1-12 month value.
        day: 1-31 day value.
        hour: 0-23 hour value.
        minute: 0-59 minute value.
        year: Year carried in from the most recent month separator, or
            None when none has been seen.
        today: Reference date used for fallback year inference.

    Returns:
        A naive datetime in the venue's local time.
    """
    from datetime import datetime as _datetime

    if year is not None:
        return _datetime(year, month, day, hour, minute)
    return build_event_datetime(
        month=month, day=day, hour=hour, minute=minute, today=today
    )


def _parse_short_date(value: str) -> tuple[int | None, int | None]:
    """Parse a date sticker like ``Sat, Apr 25`` into (month, day).

    Args:
        value: Stripped sticker text.

    Returns:
        Tuple of (month, day) as 1-based integers, or (None, None) when
        the string cannot be parsed.
    """
    if not value:
        return None, None
    cleaned = value.replace(",", " ")
    tokens = [tok for tok in cleaned.split() if tok]
    month: int | None = None
    day: int | None = None
    for token in tokens:
        if month is None:
            candidate = parse_month_name(token)
            if candidate is not None:
                month = candidate
                continue
        if day is None:
            try:
                parsed = int(token)
            except ValueError:
                continue
            if 1 <= parsed <= 31:
                day = parsed
    return month, day


def _separator_year(node: Tag) -> int | None:
    """Extract the year from a ``.rhp-events-list-separator-month`` node.

    The plugin renders separators like ``<span>April 2026</span>`` as
    the only text inside the wrapper. Absent or malformed separators
    return None and are ignored.

    Args:
        node: The separator span.

    Returns:
        The 4-digit year, or None when the text doesn't end with one.
    """
    text = node.get_text(" ", strip=True)
    if not text:
        return None
    for token in text.split():
        if len(token) == 4 and token.isdigit():
            year = int(token)
            if 2000 <= year <= 2100:
                return year
    return None


def _extract_cta(item: Tag) -> tuple[str | None, str | None]:
    """Pull the primary ticket CTA link and its status class.

    Args:
        item: The card's container element.

    Returns:
        Tuple of (ticket URL, status). Status mirrors the modifier
        class on ``span.rhp-event-cta`` (e.g. ``on-sale`` becomes
        ``on sale``). Either or both may be None when the row has no
        CTA span yet (common for newly-announced shows).
    """
    cta = item.select_one("span.rhp-event-cta")
    if not isinstance(cta, Tag):
        return None, None
    classes = [c for c in (cta.get("class") or []) if c != "rhp-event-cta"]
    status: str | None = None
    if classes:
        status = classes[0].replace("-", " ").strip().lower() or None
    href: str | None = None
    link = cta.select_one("a[href]")
    if isinstance(link, Tag):
        raw = link.get("href")
        if isinstance(raw, str) and raw.strip():
            href = raw.strip()
    return href, status


def _extract_image(item: Tag) -> str | None:
    """Return the first event flyer image URL.

    Args:
        item: The card's container element.

    Returns:
        Absolute image URL, or None when the row has no flyer.
    """
    img = item.select_one("img.rhp-event__image--list[src]")
    if not isinstance(img, Tag):
        return None
    src = img.get("src")
    if not isinstance(src, str) or not src.strip():
        return None
    return src.strip()


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
