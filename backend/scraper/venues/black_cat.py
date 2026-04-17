"""Custom scraper for Black Cat (Washington, DC).

Black Cat publishes its full calendar at
``https://www.blackcatdc.com/schedule.html`` as plain HTML. Each
show is a ``.show`` block that contains a date (e.g. ``Thursday
April 16`` — no year), the headline band as a link, one or more
``.support`` elements for openers, a ``.show-text`` doors line, and
an etix ticket URL.

Black Cat's homepage does not publish JSON-LD and the ``side-schedule.html``
iframe only carries ~5 highlighted shows, so the schedule page is the
single source of truth for the full calendar.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any
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

logger = get_logger(__name__)

BLACK_CAT_URL = "https://www.blackcatdc.com/schedule.html"
VENUE_EXTERNAL_ID = "black-cat"

# Examples:  "Thursday April 16"  "Monday April 20"  "Friday May 2"
_DATE_PATTERN = re.compile(
    r"(?:[A-Za-z]+\s+)?(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})"
)
# "Doors at 7:30", "Doors 8:00 pm", "DOORS AT 7 PM"
_DOORS_PATTERN = re.compile(
    r"doors?\s*(?:at)?\s*(?P<time>\d{1,2}(?::\d{2})?\s*(?:[ap]m?)?)",
    re.IGNORECASE,
)


class BlackCatScraper(BaseScraper):
    """Scrape upcoming shows from blackcatdc.com/schedule.html.

    Attributes:
        url: Full schedule page URL.
        today: Optional reference date used for year inference in tests.
    """

    source_platform = "black_cat"

    def __init__(
        self,
        *,
        url: str = BLACK_CAT_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Black Cat scraper.

        Args:
            url: Override for the schedule page URL.
            today: Optional reference date used to infer years for
                the schedule's year-less dates. Defaults to today.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch Black Cat's schedule and yield one RawEvent per show.

        Yields:
            RawEvent instances for every show block on the page.
        """
        logger.info("BlackCatScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse ``.show`` blocks on the schedule page into RawEvents.

        Args:
            html: The fully-fetched schedule HTML.

        Yields:
            RawEvent for every parseable ``.show`` element.
        """
        soup = BeautifulSoup(html, "html.parser")
        shows = soup.select("#main-calendar .show")
        logger.info("BlackCatScraper found %d show blocks.", len(shows))

        count = 0
        for show in shows:
            event = self._show_to_raw_event(show)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "BlackCatScraper parsed no events on %s — "
                "schedule markup may have changed.",
                self.url,
            )
        else:
            logger.info("BlackCatScraper yielded %d events.", count)

    def _show_to_raw_event(self, show: Tag) -> RawEvent | None:
        """Convert one ``.show`` element into a ``RawEvent``.

        Args:
            show: The BeautifulSoup tag wrapping a single show listing.

        Returns:
            A populated RawEvent, or None when required fields are
            missing (headline or parseable date).
        """
        headline_el = show.select_one(".headline")
        title = _text(headline_el)
        if not title:
            return None

        date_el = show.select_one(".date")
        starts_at = self._parse_show_date(_text(date_el), show)
        if starts_at is None:
            return None

        detail_url: str | None = None
        headline_link = show.select_one(".headline a[href]")
        if isinstance(headline_link, Tag):
            href = headline_link.get("href")
            if isinstance(href, str) and href.strip():
                detail_url = urljoin(self.url, href.strip())

        ticket_url: str | None = None
        for link in show.select("a[href]"):
            href = link.get("href")
            if isinstance(href, str) and "etix.com" in href:
                ticket_url = href.strip()
                break

        image_url: str | None = None
        img = show.select_one(".band-photo-sm img[src], img[src]")
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = urljoin(self.url, src.strip())

        artists = [title]
        for support in show.select(".support"):
            name = _text(support)
            if name and name not in artists:
                artists.append(name)

        description = _text(show.select_one(".show-text"))

        raw_data: dict[str, Any] = {
            "title": title,
            "date_text": _text(date_el),
            "support": [_text(s) for s in show.select(".support") if _text(s)],
            "show_text": description,
            "detail_url": detail_url,
            "source": "black_cat_schedule_html",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=detail_url or ticket_url or self.url,
            raw_data=raw_data,
            artists=artists,
            description=description,
            ticket_url=ticket_url,
            image_url=image_url,
        )

    def _parse_show_date(
        self,
        date_text: str | None,
        show: Tag,
    ) -> datetime | None:
        """Parse Black Cat's date string into a datetime, folding in doors time.

        The ``.date`` element looks like ``Thursday April 16`` with no
        year. We derive the year via :func:`backend.scraper.base.dates.infer_year`
        and read the show time from the ``.show-text`` "Doors at ..."
        line when present.

        Args:
            date_text: Raw text from ``.date``.
            show: The parent ``.show`` element (used to pull doors time).

        Returns:
            A venue-local ``datetime``, or None when the date cannot be parsed.
        """
        if not date_text:
            return None

        match = _DATE_PATTERN.search(date_text)
        if match is None:
            return None

        month = parse_month_name(match.group("month"))
        try:
            day = int(match.group("day"))
        except ValueError:
            return None
        if month is None or not (1 <= day <= 31):
            return None

        doors_time: tuple[int, int] | None = None
        show_text = _text(show.select_one(".show-text"))
        if show_text:
            doors_match = _DOORS_PATTERN.search(show_text)
            if doors_match:
                raw_time = doors_match.group("time").strip()
                if not re.search(r"[ap]m", raw_time, re.IGNORECASE):
                    raw_time = f"{raw_time} pm"
                doors_time = parse_clock_time(raw_time)

        hour, minute = doors_time if doors_time is not None else (20, 0)
        return build_event_datetime(
            month=month, day=day, hour=hour, minute=minute, today=self.today
        )


def _text(element: Tag | None) -> str | None:
    """Return the stripped text of an element, or None when absent/blank.

    Args:
        element: BeautifulSoup tag or None.

    Returns:
        Stripped text content, or None.
    """
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return text or None
