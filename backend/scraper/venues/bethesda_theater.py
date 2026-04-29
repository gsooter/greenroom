"""Custom scraper for Bethesda Theater (Bethesda, MD).

Bethesda Theater publishes its calendar on a Squarespace homepage at
https://www.bethesdatheater.com/. The site does not expose JSON-LD or
a Squarespace events collection — instead the team manually composes
each show as a Squarespace ``.sqs-row`` block containing:

- an ``h3`` show title
- one or more ``h4`` date strings such as ``SAT APRIL 25| 8:00PM``
- a buy-tickets button linking to InstantSeats or TicketWeb

Since shows are hand-built blocks, the markup is not perfectly uniform
and a few multi-night residencies are bundled into a single row with
one ticket link covering both nights. We only emit rows that carry a
single ticket link as their narrowest ``.sqs-row`` ancestor; the
infrequent multi-night residencies are left to a future iteration.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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

BETHESDA_THEATER_URL = "https://www.bethesdatheater.com/"
VENUE_EXTERNAL_ID = "bethesda-theater"

# Examples:
#   "SAT APRIL 25| 8:00PM"
#   "TUES MAY 5 | 7:00PM"
#   "FRI AUG 28| 8:30PM"
#   "SAT NOV 7 | 8:00PM"
_DATE_PATTERN = re.compile(
    r"(?:[A-Z]{3,5}\s+)?"  # optional weekday prefix
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})"
    r"\s*\|?\s*"
    r"(?P<time>\d{1,2}(?::\d{2})?\s*(?:[ap]m?\.?)?)?",
    re.IGNORECASE,
)


def _is_ticket_link(href: str) -> bool:
    """Return True for InstantSeats or TicketWeb URLs.

    Args:
        href: Anchor href attribute value.

    Returns:
        True when the link points at one of Bethesda's two ticketing
        providers, False otherwise.
    """
    return "instantseats.com" in href or "ticketweb.com" in href


class BethesdaTheaterScraper(BaseScraper):
    """Scrape upcoming shows from bethesdatheater.com.

    Attributes:
        url: The Bethesda Theater homepage URL.
        today: Optional reference date used for year inference in tests.
    """

    source_platform = "bethesda_theater"

    def __init__(
        self,
        *,
        url: str = BETHESDA_THEATER_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Bethesda Theater scraper.

        Args:
            url: Override for the homepage URL.
            today: Optional reference date used for year inference. Tests
                pass a fixed value; production scrapes use ``date.today()``.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the homepage and yield one RawEvent per Bethesda show row.

        Yields:
            RawEvent for every Squarespace row that resolves to a single
            show with parseable date and ticket URL.
        """
        logger.info("BethesdaTheaterScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Walk Squarespace ``.sqs-row`` blocks and emit RawEvents.

        Args:
            html: The fully-fetched homepage HTML.

        Yields:
            RawEvent for every row that survives the
            single-ticket-link narrowest-ancestor filter and has a
            parseable date.
        """
        soup = BeautifulSoup(html, "html.parser")
        rows = self._find_show_rows(soup)
        logger.info("BethesdaTheaterScraper found %d candidate rows.", len(rows))

        count = 0
        for row in rows:
            event = self._row_to_raw_event(row)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "BethesdaTheaterScraper parsed no events on %s — "
                "Squarespace markup may have changed.",
                self.url,
            )
        else:
            logger.info("BethesdaTheaterScraper yielded %d events.", count)

    def _find_show_rows(self, soup: BeautifulSoup) -> list[Tag]:
        """Return the narrowest sqs-row containing exactly one ticket URL.

        The Bethesda homepage nests rows: an outer page section is itself
        an ``.sqs-row`` containing many inner show rows. We want the
        *innermost* row per ticket URL so that headings/images don't
        bleed across shows.

        Args:
            soup: The parsed homepage.

        Returns:
            Ordered list of show-row Tags.
        """
        rows: list[Tag] = []
        for row in soup.select("div.sqs-row"):
            ticket_hrefs = {
                href
                for a in row.select("a[href]")
                if isinstance(href := a.get("href"), str) and _is_ticket_link(href)
            }
            if len(ticket_hrefs) != 1:
                continue
            inner = row.select("div.sqs-row")
            inner_has_same_ticket = any(
                {
                    href
                    for a in sub.select("a[href]")
                    if isinstance(href := a.get("href"), str) and _is_ticket_link(href)
                }
                == ticket_hrefs
                for sub in inner
                if sub is not row
            )
            if inner_has_same_ticket:
                continue
            rows.append(row)
        return rows

    def _row_to_raw_event(self, row: Tag) -> RawEvent | None:
        """Convert one show row into a ``RawEvent``.

        Args:
            row: The narrowest ``.sqs-row`` element wrapping a single show.

        Returns:
            A populated RawEvent, or None when the row lacks a parseable
            title/date/ticket URL combination.
        """
        title_el = row.select_one("h3, h2, h1")
        title = _text(title_el)
        if not title:
            return None

        date_text: str | None = None
        starts_at = None
        for h4 in row.select("h4"):
            candidate_text = _text(h4)
            if not candidate_text:
                continue
            parsed = self._parse_date_text(candidate_text)
            if parsed is not None:
                date_text = candidate_text
                starts_at = parsed
                break
        if starts_at is None:
            return None

        ticket_url: str | None = None
        for a in row.select("a[href]"):
            href = a.get("href")
            if isinstance(href, str) and _is_ticket_link(href):
                ticket_url = href.strip()
                break
        if not ticket_url:
            return None

        image_url: str | None = None
        img = row.select_one("img[src]")
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = src.strip()

        raw_data: dict[str, Any] = {
            "title": title,
            "date_text": date_text,
            "ticket_provider": _ticket_provider(ticket_url),
            "source": "bethesda_theater_html",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=ticket_url,
            raw_data=raw_data,
            artists=[title],
            ticket_url=ticket_url,
            image_url=image_url,
        )

    def _parse_date_text(self, text: str) -> datetime | None:
        """Parse a Bethesda date heading like ``SAT APRIL 25| 8:00PM``.

        Args:
            text: Raw heading text.

        Returns:
            A naive ``datetime`` in venue-local time, or None when the
            text doesn't match the expected month + day shape.
        """
        match = _DATE_PATTERN.search(text)
        if match is None:
            return None
        month = parse_month_name(match.group("month"))
        try:
            day = int(match.group("day"))
        except ValueError:
            return None
        if month is None or not (1 <= day <= 31):
            return None

        clock_text = match.group("time")
        clock = parse_clock_time(clock_text) if clock_text else None
        hour, minute = clock if clock is not None else (20, 0)
        return build_event_datetime(
            month=month, day=day, hour=hour, minute=minute, today=self.today
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


def _ticket_provider(url: str) -> str:
    """Identify which ticketing platform a Bethesda link points at.

    Args:
        url: A buy-tickets URL.

    Returns:
        ``"instantseats"``, ``"ticketweb"``, or ``"unknown"``.
    """
    if "instantseats.com" in url:
        return "instantseats"
    if "ticketweb.com" in url:
        return "ticketweb"
    return "unknown"
