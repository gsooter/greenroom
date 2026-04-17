"""Ticketmaster Discovery API scraper.

Fetches events from the Ticketmaster Discovery API v2 for a specific
venue and yields RawEvent instances. Handles pagination and respects
rate limits with exponential backoff.
"""

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import requests

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

logger = get_logger(__name__)

DISCOVERY_API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
MAX_PAGES = 10
PAGE_SIZE = 50
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0


class TicketmasterScraper(BaseScraper):
    """Scraper for venues listed on Ticketmaster.

    Uses the Discovery API v2 to fetch upcoming events for a single
    venue. Most DC venues are on Ticketmaster, so this one scraper
    covers the majority of the fleet.

    Attributes:
        venue_id: Ticketmaster venue ID.
        venue_name: Human-readable venue name for logging.
        api_key: Ticketmaster API key from config.
    """

    source_platform = "ticketmaster"

    def __init__(
        self,
        *,
        venue_id: str,
        venue_name: str,
        api_key: str | None = None,
    ) -> None:
        """Initialize the Ticketmaster scraper for a specific venue.

        Args:
            venue_id: Ticketmaster venue ID (e.g., "KovZpa2ywe").
            venue_name: Human-readable venue name for logging.
            api_key: Optional explicit Ticketmaster Discovery API key.
                When None, reads ``TICKETMASTER_API_KEY`` via
                :func:`backend.core.config.get_settings`. Tests and the
                smoke script pass this directly to avoid touching env.
        """
        self.venue_id = venue_id
        self.venue_name = venue_name
        if api_key is None:
            api_key = get_settings().ticketmaster_api_key
        self.api_key = api_key

    def scrape(self) -> Iterator[RawEvent]:
        """Scrape upcoming events from Ticketmaster for this venue.

        Paginates through the Discovery API results and yields
        a RawEvent for each event found. Handles rate limiting
        with exponential backoff.

        Yields:
            RawEvent instances for each discovered event.
        """
        logger.info(
            "Scraping Ticketmaster for '%s' (venue_id=%s)",
            self.venue_name,
            self.venue_id,
        )

        for page in range(MAX_PAGES):
            response_data = self._fetch_page(page)
            if response_data is None:
                break

            embedded = response_data.get("_embedded")
            if not embedded:
                break

            events = embedded.get("events", [])
            if not events:
                break

            for event_data in events:
                raw_event = self._parse_event(event_data)
                if raw_event is not None:
                    yield raw_event

            # Check if there are more pages
            page_info = response_data.get("page", {})
            total_pages = page_info.get("totalPages", 0)
            if page + 1 >= total_pages:
                break

        logger.info(
            "Finished scraping Ticketmaster for '%s'.", self.venue_name
        )

    def _fetch_page(self, page: int) -> dict[str, Any] | None:
        """Fetch a single page of results from the Discovery API.

        Implements exponential backoff for rate limiting (HTTP 429).

        Args:
            page: Zero-indexed page number.

        Returns:
            Parsed JSON response dict, or None if the request failed.
        """
        params = {
            "apikey": self.api_key,
            "venueId": self.venue_id,
            "sort": "date,asc",
            "size": PAGE_SIZE,
            "page": page,
        }

        backoff = INITIAL_BACKOFF
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(
                    DISCOVERY_API_URL,
                    params=params,
                    timeout=30,
                )

                if response.status_code == 429:
                    logger.warning(
                        "Rate limited by Ticketmaster (attempt %d/%d), "
                        "backing off %.1fs.",
                        attempt + 1,
                        MAX_RETRIES,
                        backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                response.raise_for_status()
                return response.json()

            except requests.RequestException as e:
                logger.error(
                    "Ticketmaster API error (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= 2

        logger.error(
            "Failed to fetch page %d for '%s' after %d attempts.",
            page,
            self.venue_name,
            MAX_RETRIES,
        )
        return None

    def _parse_event(self, data: dict[str, Any]) -> RawEvent | None:
        """Parse a Ticketmaster event JSON object into a RawEvent.

        Args:
            data: Single event object from the Discovery API response.

        Returns:
            A RawEvent instance, or None if the event data is invalid.
        """
        try:
            title = data.get("name", "")
            if not title:
                return None

            event_id = data.get("id", "")

            # Parse start datetime
            dates = data.get("dates", {})
            start_info = dates.get("start", {})
            start_dt = self._parse_datetime(start_info)
            if start_dt is None:
                logger.debug("Skipping event '%s': no valid start date.", title)
                return None

            # Parse artists from attractions
            artists = self._extract_artists(data)

            # Parse pricing
            min_price, max_price = self._extract_prices(data)

            # Parse images — pick the largest
            image_url = self._extract_image(data)

            # Build ticket URL
            ticket_url = data.get("url")

            # Source URL
            source_url = data.get("url")

            return RawEvent(
                title=title,
                venue_external_id=self.venue_id,
                starts_at=start_dt,
                source_url=source_url or "",
                raw_data=data,
                artists=artists,
                description=data.get("info"),
                ticket_url=ticket_url,
                min_price=min_price,
                max_price=max_price,
                image_url=image_url,
            )

        except Exception as e:
            logger.error(
                "Failed to parse Ticketmaster event: %s — %s",
                data.get("name", "unknown"),
                e,
            )
            return None

    def _parse_datetime(
        self, start_info: dict[str, Any]
    ) -> datetime | None:
        """Parse a Ticketmaster start date/time object.

        The Discovery API returns ``localDate`` and ``localTime`` in the
        venue's local timezone. We keep the datetime naive (no tzinfo)
        so it matches the venue-local convention used across every other
        scraper in this project; the storage layer is responsible for
        attaching a venue timezone when needed.

        Args:
            start_info: The 'start' object from the dates field.

        Returns:
            A naive venue-local ``datetime``, or None if unparseable.
        """
        date_str = start_info.get("localDate")
        if not date_str:
            return None

        time_str = start_info.get("localTime", "20:00:00")
        try:
            return datetime.fromisoformat(f"{date_str}T{time_str}")
        except ValueError:
            return None

    def _extract_artists(self, data: dict[str, Any]) -> list[str]:
        """Extract artist names from a Ticketmaster event.

        Args:
            data: Full event object from the Discovery API.

        Returns:
            List of artist/performer name strings.
        """
        artists: list[str] = []
        embedded = data.get("_embedded", {})
        attractions = embedded.get("attractions", [])
        for attraction in attractions:
            name = attraction.get("name")
            if name:
                artists.append(name)
        return artists

    def _extract_prices(
        self, data: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        """Extract min/max ticket prices from a Ticketmaster event.

        Args:
            data: Full event object from the Discovery API.

        Returns:
            Tuple of (min_price, max_price), either may be None.
        """
        price_ranges = data.get("priceRanges", [])
        if not price_ranges:
            return None, None

        min_price: float | None = None
        max_price: float | None = None

        for pr in price_ranges:
            p_min = pr.get("min")
            p_max = pr.get("max")
            if p_min is not None:
                if min_price is None or p_min < min_price:
                    min_price = p_min
            if p_max is not None:
                if max_price is None or p_max > max_price:
                    max_price = p_max

        return min_price, max_price

    def _extract_image(self, data: dict[str, Any]) -> str | None:
        """Extract the best image URL from a Ticketmaster event.

        Picks the image with the largest width from the images array.

        Args:
            data: Full event object from the Discovery API.

        Returns:
            Image URL string, or None if no images available.
        """
        images = data.get("images", [])
        if not images:
            return None

        best = max(images, key=lambda img: img.get("width", 0))
        return best.get("url")
