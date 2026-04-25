"""Custom scraper for The Hamilton Live (Washington, DC).

The Hamilton runs its own ticketing on ``live.thehamiltondc.com`` via
the See Tickets v2.0 WordPress plugin. The plugin exposes the event
catalog through the standard WP REST endpoint
``/wp-json/wp/v2/event``, but the WP REST payload itself does **not**
include the show date — that lives only inside the rendered detail
page as ``.dates`` (MM/DD/YYYY) and ``.detail_event_time`` (clock
time).

Strategy:
1. Page through ``/wp-json/wp/v2/event`` to enumerate every event
   record (id, slug, title, link, featured image).
2. For each record, fetch the detail page and parse the See Tickets
   plugin block to recover the show date, time, ticket URL, price
   range, and status.

The parent ticketing host ``wl.seetickets.us`` is Cloudflare-protected
and not directly scrapeable, so going through the venue's own
WordPress plugin is the only first-party path to the calendar.
"""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from backend.core.logging import get_logger
from backend.scraper.base.dates import parse_clock_time
from backend.scraper.base.http import HttpFetchError, fetch_html
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

logger = get_logger(__name__)

HAMILTON_REST_URL = "https://live.thehamiltondc.com/wp-json/wp/v2/event"
HAMILTON_DETAIL_BASE = "https://live.thehamiltondc.com/"
VENUE_EXTERNAL_ID = "the-hamilton"

_DATE_PATTERN = re.compile(r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})")
_PRICE_PATTERN = re.compile(r"\$(?P<amount>\d+(?:\.\d{2})?)")


class TheHamiltonScraper(BaseScraper):
    """Scrape upcoming shows from live.thehamiltondc.com.

    The Hamilton's WP REST event collection lists the full calendar but
    omits dates; each detail page renders the See Tickets plugin block
    that does include the date and ticket URL.

    Attributes:
        rest_url: WP REST endpoint listing event posts.
        per_page: Page size for the WP REST collection request.
        max_pages: Hard cap on pagination to bound runtime.
    """

    source_platform = "the_hamilton"

    def __init__(
        self,
        *,
        rest_url: str = HAMILTON_REST_URL,
        per_page: int = 100,
        max_pages: int = 10,
    ) -> None:
        """Configure the Hamilton scraper.

        Args:
            rest_url: Override for the WP REST event endpoint.
            per_page: Number of records requested per WP REST page.
            max_pages: Safety cap on the number of paginated requests.
        """
        self.rest_url = rest_url
        self.per_page = per_page
        self.max_pages = max_pages

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the Hamilton calendar and yield one RawEvent per show.

        Yields:
            RawEvent instances for every event resolved from a detail page.
        """
        records = list(self._fetch_event_records())
        logger.info(
            "TheHamiltonScraper: %d event records from %s", len(records), self.rest_url
        )

        count = 0
        for record in records:
            link = record.get("link")
            if not isinstance(link, str) or not link.strip():
                continue
            try:
                detail_html = fetch_html(link)
            except HttpFetchError as exc:
                logger.warning(
                    "TheHamiltonScraper: skipping %s — detail fetch failed: %s",
                    link,
                    exc,
                )
                continue

            event = self._detail_to_raw_event(record, link, detail_html)
            if event is not None:
                count += 1
                yield event

        if count == 0:
            logger.warning(
                "TheHamiltonScraper parsed no events — REST or detail markup "
                "may have changed."
            )
        else:
            logger.info("TheHamiltonScraper yielded %d events.", count)

    def _fetch_event_records(self) -> Iterator[dict[str, Any]]:
        """Page through the WP REST event endpoint.

        Yields:
            Each event record as a dict — at minimum carrying ``id``,
            ``slug``, ``title``, and ``link`` keys.
        """
        for page in range(1, self.max_pages + 1):
            url = f"{self.rest_url}?per_page={self.per_page}&page={page}"
            try:
                body = fetch_html(url)
            except HttpFetchError as exc:
                logger.warning("TheHamiltonScraper: REST page %d failed: %s", page, exc)
                return

            try:
                payload = json.loads(body)
            except ValueError as exc:
                logger.warning(
                    "TheHamiltonScraper: REST page %d returned non-JSON: %s",
                    page,
                    exc,
                )
                return

            if not isinstance(payload, list) or not payload:
                return

            for record in payload:
                if isinstance(record, dict):
                    yield record

            if len(payload) < self.per_page:
                return

    def _detail_to_raw_event(
        self,
        record: dict[str, Any],
        detail_url: str,
        detail_html: str,
    ) -> RawEvent | None:
        """Parse a single event detail page into a ``RawEvent``.

        Args:
            record: The WP REST record (used for id, slug, raw payload).
            detail_url: Canonical detail-page URL on live.thehamiltondc.com.
            detail_html: Full HTML body of the detail page.

        Returns:
            A populated RawEvent, or None when the page lacks the
            See Tickets block or its date span.
        """
        soup = BeautifulSoup(detail_html, "html.parser")
        block = soup.select_one("#seetickets .single-view-item")
        if block is None:
            logger.warning(
                "TheHamiltonScraper: %s missing #seetickets block.", detail_url
            )
            return None

        starts_at = self._parse_starts_at(block)
        if starts_at is None:
            return None

        title = _record_title(record) or _text(block.select_one("h1"))
        if not title:
            return None

        ticket_url = _first_link_href(block, "a.event_button.get-tickets[href]")
        if ticket_url is None:
            ticket_url = _first_link_href(block, "h1 a[href]")

        image_url: str | None = None
        img = block.select_one(".list-img img[src]")
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = urljoin(detail_url, src.strip())

        min_price, max_price = _parse_price_range(
            _text(block.select_one(".detail_price_range .name"))
        )
        status = _text(block.select_one(".detail_ticket_status .name"))
        description = _text(soup.select_one(".event-description"))

        raw_data: dict[str, Any] = {
            "wp_id": record.get("id"),
            "wp_slug": record.get("slug"),
            "title": title,
            "date_text": _text(block.select_one(".dates")),
            "event_date_text": _text(block.select_one(".detail_event_date .name")),
            "event_time_text": _text(block.select_one(".detail_event_time .name")),
            "price_range_text": _text(block.select_one(".detail_price_range .name")),
            "status": status,
            "source": "the_hamilton_seetickets_v2",
        }

        return RawEvent(
            title=title,
            venue_external_id=VENUE_EXTERNAL_ID,
            starts_at=starts_at,
            source_url=detail_url,
            raw_data=raw_data,
            artists=[title],
            description=description,
            ticket_url=ticket_url,
            image_url=image_url,
            min_price=min_price,
            max_price=max_price,
        )

    def _parse_starts_at(self, block: Tag) -> datetime | None:
        """Parse the See Tickets block's date and time into a datetime.

        Args:
            block: The ``#seetickets .single-view-item`` element.

        Returns:
            A naive venue-local ``datetime``, or None when the date span
            is absent or unparseable.
        """
        from datetime import datetime as _dt

        date_text = _text(block.select_one(".dates"))
        if not date_text:
            return None
        match = _DATE_PATTERN.search(date_text)
        if match is None:
            return None
        try:
            month = int(match.group("m"))
            day = int(match.group("d"))
            year = int(match.group("y"))
        except ValueError:
            return None
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None

        time_text = _text(block.select_one(".detail_event_time .name"))
        clock = parse_clock_time(time_text)
        hour, minute = clock if clock is not None else (20, 0)

        try:
            return _dt(year, month, day, hour, minute)
        except ValueError:
            return None


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


def _record_title(record: dict[str, Any]) -> str | None:
    """Extract the unescaped title from a WP REST event record.

    Args:
        record: The WP REST event payload.

    Returns:
        The decoded plain-text title, or None when absent.
    """
    title_field = record.get("title")
    if isinstance(title_field, dict):
        rendered = title_field.get("rendered")
        if isinstance(rendered, str) and rendered.strip():
            return html.unescape(rendered).strip()
    if isinstance(title_field, str) and title_field.strip():
        return html.unescape(title_field).strip()
    return None


def _first_link_href(scope: Tag, selector: str) -> str | None:
    """Return the first non-empty ``href`` matching ``selector`` within ``scope``.

    Args:
        scope: BeautifulSoup tag to search inside.
        selector: CSS selector targeting an anchor with an href.

    Returns:
        The trimmed href string, or None when no matching anchor exists.
    """
    link = scope.select_one(selector)
    if not isinstance(link, Tag):
        return None
    href = link.get("href")
    if isinstance(href, str) and href.strip():
        return href.strip()
    return None


def _parse_price_range(text: str | None) -> tuple[float | None, float | None]:
    """Parse a ``$min-$max`` See Tickets price string into floats.

    Handles the common See Tickets shapes:
    - ``$75.00-$115.00`` → (75.00, 115.00)
    - ``$45.00`` → (45.00, 45.00)
    - empty / unparseable → (None, None)

    Args:
        text: Raw price-range text from ``.detail_price_range .name``.

    Returns:
        Tuple of (min_price, max_price) as floats, or (None, None).
    """
    if not text:
        return (None, None)
    matches = _PRICE_PATTERN.findall(text)
    if not matches:
        return (None, None)
    try:
        amounts = [float(m) for m in matches]
    except ValueError:
        return (None, None)
    if not amounts:
        return (None, None)
    if len(amounts) == 1:
        return (amounts[0], amounts[0])
    return (min(amounts), max(amounts))
