"""Ticketmaster pricing provider.

Tier A surface — Ticketmaster's Discovery API v2 returns face-value
``priceRanges`` (min/max) plus the canonical buy URL on every event
response. Less rich than SeatGeek (no listing count, no average) but
covers the largest single inventory on the platform: most major-room
DC and Baltimore venues route through Ticketmaster.

Lookup mirrors :class:`SeatGeekPricingProvider`: direct ``events/{id}``
when the underlying scrape originated on Ticketmaster, search by
venueId + keyword + datetime window otherwise.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import requests

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.pricing.base import BasePricingProvider, PriceQuote

if TYPE_CHECKING:
    from backend.data.models.events import Event


logger = get_logger(__name__)

TICKETMASTER_BASE_URL = "https://app.ticketmaster.com/discovery/v2"
DEFAULT_TIMEOUT_SECONDS = 15
DATE_WINDOW_HOURS = 24


class TicketmasterPricingProvider(BasePricingProvider):
    """Provider that quotes prices from the Ticketmaster Discovery API.

    Auth is a single ``apikey`` query parameter. The same key the
    venue scraper uses also drives pricing — there's no separate
    pricing scope.

    Attributes:
        name: Persisted as ``ticket_pricing_snapshots.source`` and
            ``event_pricing_links.source``.
        api_key: Ticketmaster Discovery API key.
        timeout: Per-request HTTP timeout in seconds.
    """

    name = "ticketmaster"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the Ticketmaster pricing provider.

        Args:
            api_key: Explicit Ticketmaster API key. When ``None``, reads
                ``settings.ticketmaster_api_key`` so production hits the
                same env var as the scraper.
            timeout: HTTP timeout in seconds. Defaults match the
                SeatGeek provider for orchestrator parity.
        """
        if api_key is None:
            api_key = get_settings().ticketmaster_api_key
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, event: Event) -> PriceQuote | None:
        """Look up Ticketmaster pricing for one event.

        Args:
            event: The event to price.

        Returns:
            A :class:`PriceQuote` on a successful match, ``None`` when
            Ticketmaster doesn't carry the event or the search returns
            no candidates.
        """
        if getattr(event, "source_platform", None) == self.name and getattr(
            event, "external_id", None
        ):
            found, quote = self._fetch_by_id(event.external_id)
            if found:
                return quote

        return self._fetch_by_search(event)

    def _fetch_by_id(self, tm_event_id: str) -> tuple[bool, PriceQuote | None]:
        """Hit the events-by-id endpoint and parse the response.

        Args:
            tm_event_id: Ticketmaster event id (e.g. ``"vvG1HZ9...AB"``).

        Returns:
            Tuple of ``(found_upstream, quote)``. ``found_upstream`` is
            ``False`` only on a 404; a 200 with empty ``priceRanges``
            still counts as found and the caller skips the search
            fallback.
        """
        url = f"{TICKETMASTER_BASE_URL}/events/{tm_event_id}.json"
        status, payload = self._get(url, params={})
        if status == 404:
            return False, None
        if payload is None:
            return True, None
        return True, self._payload_to_quote(payload)

    def _fetch_by_search(self, event: Event) -> PriceQuote | None:
        """Search for an event and quote the first candidate.

        Filters by venueId when the venue carries a Ticketmaster
        mapping, by a ±24h ``startDateTime``/``endDateTime`` window,
        and by a keyword pulled from the event's headline artist or
        title. Picks the first result — the Discovery API's relevance
        ranking is good enough that re-ranking locally rarely improves
        accuracy.

        Args:
            event: The event to search for.

        Returns:
            A :class:`PriceQuote` for the best match, ``None`` when
            the search returned nothing.
        """
        params: dict[str, Any] = {"size": 5, "sort": "date,asc"}

        venue_id = self._venue_id(event)
        if venue_id:
            params["venueId"] = venue_id

        starts_at = getattr(event, "starts_at", None)
        if starts_at is not None:
            window = timedelta(hours=DATE_WINDOW_HOURS)
            params["startDateTime"] = (starts_at - window).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            params["endDateTime"] = (starts_at + window).strftime("%Y-%m-%dT%H:%M:%SZ")

        keyword = self._search_query(event)
        if keyword:
            params["keyword"] = keyword

        _status, payload = self._get(
            f"{TICKETMASTER_BASE_URL}/events.json", params=params
        )
        if payload is None:
            return None

        embedded = payload.get("_embedded") or {}
        events = embedded.get("events") or []
        if not events:
            return None

        return self._payload_to_quote(events[0])

    def _payload_to_quote(self, payload: dict[str, Any]) -> PriceQuote | None:
        """Translate a Ticketmaster event JSON object into a :class:`PriceQuote`.

        ``priceRanges`` is the only place Discovery exposes a number —
        we take the lowest ``min`` and highest ``max`` across types so
        a 'standard'+'platinum' split still yields a usable range.
        ``dates.status.code`` of ``"offsale"`` or ``"cancelled"`` flips
        ``is_active`` off without dropping the URL.

        Args:
            payload: A single event object from the Discovery API.

        Returns:
            The parsed :class:`PriceQuote`, or ``None`` when the
            payload has neither a buy URL nor any priceRanges entry.
        """
        url = payload.get("url")
        ranges = payload.get("priceRanges") or []

        min_price: float | None = None
        max_price: float | None = None
        currency: str = "USD"
        for entry in ranges:
            entry_min = _as_float(entry.get("min"))
            entry_max = _as_float(entry.get("max"))
            if entry_min is not None:
                min_price = (
                    entry_min if min_price is None else min(min_price, entry_min)
                )
            if entry_max is not None:
                max_price = (
                    entry_max if max_price is None else max(max_price, entry_max)
                )
            entry_currency = entry.get("currency")
            if isinstance(entry_currency, str) and entry_currency:
                currency = entry_currency

        if not url and min_price is None and max_price is None:
            return None

        status_code = ((payload.get("dates") or {}).get("status") or {}).get("code")
        is_active = status_code not in {"offsale", "cancelled", "rescheduled"}

        return PriceQuote(
            source=self.name,
            min_price=min_price,
            max_price=max_price,
            average_price=None,
            listing_count=None,
            currency=currency,
            buy_url=url,
            affiliate_url=None,
            is_active=is_active,
            raw=payload,
        )

    def _venue_id(self, event: Event) -> str | None:
        """Return the Ticketmaster venue id for the event's venue, if mapped.

        Args:
            event: The event whose venue we're inspecting.

        Returns:
            The string Ticketmaster venue id when
            ``event.venue.external_ids`` carries one, ``None`` otherwise.
        """
        venue = getattr(event, "venue", None)
        external_ids = getattr(venue, "external_ids", None) if venue else None
        if not external_ids:
            return None
        tm_id = external_ids.get(self.name)
        return str(tm_id) if tm_id else None

    def _search_query(self, event: Event) -> str | None:
        """Build the ``keyword`` parameter for an event search.

        Mirrors the SeatGeek logic — headliner artist when present,
        falling back to the event title.

        Args:
            event: The event to query for.

        Returns:
            A non-empty keyword string, or ``None`` when the event has
            neither artists nor a title.
        """
        artists: list[str] = getattr(event, "artists", None) or []
        if artists:
            headline = next((a for a in artists if a and a.strip()), None)
            if headline:
                return headline.strip()
        title: str | None = getattr(event, "title", None)
        if title and title.strip():
            return title.strip()
        return None

    def _get(
        self, url: str, *, params: dict[str, Any]
    ) -> tuple[int, dict[str, Any] | None]:
        """Issue a GET to the Discovery API with ``apikey`` attached.

        404 returns ``(404, None)`` so the caller can distinguish "not
        on Ticketmaster" from "on Ticketmaster but no quote-worthy
        body". Other 4xx/5xx raise so the orchestrator records a
        transient failure and backs off.

        Args:
            url: Fully qualified URL (no query string).
            params: Query parameters; ``apikey`` is added before
                sending.

        Returns:
            ``(status_code, payload_or_None)``.

        Raises:
            requests.RequestException: For network errors and non-404
                4xx/5xx statuses.
        """
        merged = dict(params)
        merged["apikey"] = self.api_key

        response = requests.get(url, params=merged, timeout=self.timeout)

        if response.status_code == 404:
            return 404, None
        if response.status_code >= 400:
            logger.warning(
                "Ticketmaster non-2xx response (%s) on %s",
                response.status_code,
                url,
            )
            response.raise_for_status()

        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            logger.warning("Ticketmaster returned non-JSON on %s", url)
            return response.status_code, None
        return response.status_code, data


def _as_float(value: Any) -> float | None:
    """Coerce a Ticketmaster numeric field to ``float`` without raising.

    Discovery occasionally returns prices as strings; we normalise to
    float so the orchestrator can persist a numeric column without
    branching.

    Args:
        value: Raw field from the Ticketmaster payload.

    Returns:
        ``float(value)`` when ``value`` is numeric, ``None`` otherwise.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
