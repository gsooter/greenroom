"""Custom scraper for Pearl Street Warehouse (Washington, DC).

Pearl Street Warehouse is a Union Stage Presents room that runs its own
Webflow site at https://pearlstreetwarehouse.com/. The homepage server-
renders the full upcoming calendar as ``.show-item.w-dyn-item`` Webflow
collection cards, already filtered to PSW. Each card carries:

- a date sticker (`.event-month` / `.event-day`)
- doors and show times
- a presenter badge (e.g. "All Good Presents…")
- an optional `.event-tag.sold-out` / `.event-tag.last-call` status
  badge that Webflow visually hides via ``w-condition-invisible`` when
  inactive
- a relative ``/shows/<slug>`` link to the detail page

The site does not publish JSON-LD, but the Webflow markup is
deterministic enough to parse without it.
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

PEARL_STREET_URL = "https://pearlstreetwarehouse.com/"
VENUE_EXTERNAL_ID = "pearl-street-warehouse"


class PearlStreetWarehouseScraper(BaseScraper):
    """Scrape upcoming shows from pearlstreetwarehouse.com.

    Attributes:
        url: Pearl Street's homepage / calendar URL.
        today: Optional reference date used for year inference in tests.
    """

    source_platform = "pearl_street_warehouse"

    def __init__(
        self,
        *,
        url: str = PEARL_STREET_URL,
        today: date | None = None,
    ) -> None:
        """Configure the Pearl Street scraper.

        Args:
            url: Override for the homepage URL.
            today: Optional reference date used for year inference. Tests
                pass a fixed value; production scrapes use ``date.today()``.
        """
        self.url = url
        self.today = today

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the homepage and yield one RawEvent per upcoming show.

        Yields:
            RawEvent instances for every parseable ``.show-item`` card.
        """
        logger.info("PearlStreetWarehouseScraper fetching %s", self.url)
        html = fetch_html(self.url)
        yield from self._parse(html)

    def _parse(self, html: str) -> Iterator[RawEvent]:
        """Parse the Webflow show-item collection into RawEvents.

        Args:
            html: The fully-fetched homepage HTML.

        Yields:
            RawEvent for every ``.show-item.w-dyn-item`` card with the
            minimum required title and parseable date.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".show-item.w-dyn-item")
        logger.info("PearlStreetWarehouseScraper found %d show cards.", len(items))

        count = 0
        for item in items:
            event = self._item_to_raw_event(item)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "PearlStreetWarehouseScraper parsed no events on %s — "
                "Webflow markup may have changed.",
                self.url,
            )
        else:
            logger.info("PearlStreetWarehouseScraper yielded %d events.", count)

    def _item_to_raw_event(self, item: Tag) -> RawEvent | None:
        """Convert one ``.show-item`` card into a ``RawEvent``.

        Args:
            item: BeautifulSoup tag wrapping a single show card.

        Returns:
            A populated RawEvent, or None when the card lacks a usable
            title or date.
        """
        title_el = item.select_one(".show-card-header, h3.show-card-header")
        title = _text(title_el)
        if not title:
            return None

        month_el = item.select_one(".event-month")
        day_el = item.select_one(".event-day")
        month = parse_month_name(_text(month_el) or "")
        day_text = _text(day_el) or ""
        try:
            day = int(day_text)
        except ValueError:
            day = 0
        if month is None or not (1 <= day <= 31):
            return None

        show_time = _show_clock_time(item)
        hour, minute = show_time if show_time is not None else (20, 0)
        starts_at = build_event_datetime(
            month=month, day=day, hour=hour, minute=minute, today=self.today
        )

        ticket_url: str | None = None
        link = item.select_one("a.show-card-link[href]")
        if isinstance(link, Tag):
            href = link.get("href")
            if isinstance(href, str) and href.strip():
                ticket_url = urljoin(self.url, href.strip())

        image_url: str | None = None
        img = item.select_one(".show-image-wrapper img[src]")
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = src.strip()

        artists: list[str] = [title]
        support_el = item.select_one(".uui-text-size-medium.dark-caps")
        support = _text(support_el)
        if support and support not in artists:
            artists.append(support)

        presenter_el = item.select_one(".event-grid-presenter")
        presenter = _text(presenter_el)
        if presenter and "w-dyn-bind-empty" in (presenter_el.get("class") or []):
            presenter = None

        status = _event_tag_status(item)

        raw_data: dict[str, Any] = {
            "title": title,
            "event_month": _text(month_el),
            "event_day": day_text,
            "event_day_of_week": _text(item.select_one(".event-day-day")),
            "support": support,
            "presenter": presenter,
            "status": status,
            "source": "pearl_street_warehouse_html",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=ticket_url or self.url,
            raw_data=raw_data,
            artists=artists,
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


def _show_clock_time(item: Tag) -> tuple[int, int] | None:
    """Extract the show time (not doors) from a Pearl Street card.

    Pearl Street renders ``DOORS  7:00 pm | Show  8:00 pm`` inside a
    ``.show-info`` block as a sequence of sibling divs. We walk the
    visible label/value pairs and return the value following the "Show"
    label.

    Args:
        item: The card's container element.

    Returns:
        Tuple of (hour, minute) for the show time, or None when no
        labelled show time is present.
    """
    for info in item.select(".show-info"):
        labels = [_text(child) or "" for child in info.find_all("div", recursive=False)]
        for index, label in enumerate(labels):
            if label.lower() == "show" and index + 1 < len(labels):
                clock = parse_clock_time(labels[index + 1])
                if clock is not None:
                    return clock
    return None


def _event_tag_status(item: Tag) -> str | None:
    """Detect the visible Webflow status badge on a card, if any.

    Pearl Street renders ``.event-tag.sold-out`` and
    ``.event-tag.last-call`` siblings on every card, with
    ``w-condition-invisible`` toggled when the badge does not apply.

    Args:
        item: The card's container element.

    Returns:
        Status string ("sold out", "last call", etc.), or None when no
        badge is visible.
    """
    for tag in item.select(".event-tag"):
        classes = tag.get("class") or []
        if "w-condition-invisible" in classes:
            continue
        for cls in classes:
            if cls in {"event-tag", "w-condition-invisible"}:
                continue
            return cls.replace("-", " ").strip().lower() or None
    return None
