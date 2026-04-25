"""SeatGeek pricing provider.

Tier A surface — SeatGeek's Platform API exposes ``stats`` (lowest,
highest, average prices and a listing count) on every event response,
which makes it the richest single source we have.

The provider supports two lookup modes:

1. **Direct ID lookup** — when an event was scraped from SeatGeek
   (``event.source_platform == "seatgeek"`` with an ``external_id``),
   we hit ``GET /2/events/{id}`` and parse the response. This is the
   fastest path and the only one that's deterministic.
2. **Search fallback** — for everything else we search ``GET /2/events``
   filtered by venue (when the venue carries a SeatGeek mapping),
   performer query, and a one-day datetime window, and pick the best
   match. Search is fuzzy by design so a noisy match is preferable to
   no match.

No persistence happens here; the orchestrator in
:mod:`backend.services.tickets` is responsible for turning the returned
:class:`PriceQuote` into snapshots and pricing-link rows.
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

SEATGEEK_BASE_URL = "https://api.seatgeek.com/2"
DEFAULT_TIMEOUT_SECONDS = 15
DATE_WINDOW_HOURS = 24


class SeatGeekPricingProvider(BasePricingProvider):
    """Provider that quotes prices from the SeatGeek Platform API.

    SeatGeek auth is a ``client_id`` query parameter; the secret is not
    required for read-only endpoints. The provider passes the secret
    when present so we transparently get higher rate limits as soon as
    the env var is populated.

    Attributes:
        name: Persisted as ``ticket_pricing_snapshots.source`` and
            ``event_pricing_links.source``.
        client_id: SeatGeek Platform API client id.
        client_secret: SeatGeek Platform API client secret. ``None``
            keeps the request unsigned (still works for read endpoints).
        timeout: Per-request HTTP timeout in seconds.
    """

    name = "seatgeek"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the SeatGeek provider.

        Args:
            client_id: Explicit SeatGeek Platform API client id. When
                ``None``, falls back to ``settings.seatgeek_client_id``;
                tests pass it in directly to avoid touching env state.
            client_secret: Explicit client secret. When ``None``, reads
                from ``settings.seatgeek_client_secret``. May be an
                empty string in dev environments — SeatGeek's read
                endpoints work without a secret.
            timeout: HTTP timeout in seconds. Pricing fetches are
                user-facing (the manual refresh button waits on them),
                so the default is conservative.
        """
        if client_id is None or client_secret is None:
            settings = get_settings()
            if client_id is None:
                client_id = settings.seatgeek_client_id
            if client_secret is None:
                client_secret = settings.seatgeek_client_secret
        self.client_id = client_id
        self.client_secret = client_secret or None
        self.timeout = timeout

    def fetch(self, event: Event) -> PriceQuote | None:
        """Look up SeatGeek pricing for one event.

        Tries the direct ID path first when the event itself was
        scraped from SeatGeek. Falls back to a fuzzy search by
        performer, venue, and date window only when direct lookup 404s
        — a 200 response with no quote-worthy data means the event is
        listed but has no inventory, and we should not double-spend an
        API call to search for a different match.

        Args:
            event: The event to price.

        Returns:
            A populated :class:`PriceQuote` on a successful match,
            ``None`` when SeatGeek has no opinion (search returned
            nothing, or both paths produced an empty payload).
        """
        if getattr(event, "source_platform", None) == self.name and getattr(
            event, "external_id", None
        ):
            found, quote = self._fetch_by_id(event.external_id)
            if found:
                return quote

        return self._fetch_by_search(event)

    def _fetch_by_id(self, sg_event_id: str) -> tuple[bool, PriceQuote | None]:
        """Hit ``GET /2/events/{id}`` and parse the response.

        Args:
            sg_event_id: SeatGeek event id, as stored on
                ``event.external_id`` for SeatGeek-sourced rows.

        Returns:
            Tuple of ``(found_upstream, quote)``. ``found_upstream`` is
            ``False`` only on a 404 — a 200 with an empty stats block
            still counts as found, and the caller skips the search
            fallback so we don't burn a second API call.
        """
        url = f"{SEATGEEK_BASE_URL}/events/{sg_event_id}"
        status, payload = self._get(url, params={})
        if status == 404:
            return False, None
        if payload is None:
            return True, None
        return True, self._payload_to_quote(payload)

    def _fetch_by_search(self, event: Event) -> PriceQuote | None:
        """Run a SeatGeek event search and pick the best match.

        Filters by venue when ``event.venue.external_ids`` carries a
        ``seatgeek`` entry, then by a ±24h datetime window, then by
        performer query (artists joined, falling back to title). Picks
        the first result returned — SeatGeek's relevance ordering is
        good enough that "first" beats trying to re-rank locally.

        Args:
            event: The event to search for.

        Returns:
            A :class:`PriceQuote` for the best match, or ``None`` when
            search produced no results.
        """
        params: dict[str, Any] = {}

        sg_venue_id = self._venue_id(event)
        if sg_venue_id:
            params["venue.id"] = sg_venue_id

        starts_at = getattr(event, "starts_at", None)
        if starts_at is not None:
            window = timedelta(hours=DATE_WINDOW_HOURS)
            params["datetime_utc.gte"] = (starts_at - window).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            params["datetime_utc.lte"] = (starts_at + window).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

        query = self._search_query(event)
        if query:
            params["q"] = query

        params["per_page"] = 5

        _status, payload = self._get(f"{SEATGEEK_BASE_URL}/events", params=params)
        if payload is None:
            return None

        events = payload.get("events") or []
        if not events:
            return None

        return self._payload_to_quote(events[0])

    def _payload_to_quote(self, payload: dict[str, Any]) -> PriceQuote | None:
        """Translate a SeatGeek event JSON object into a :class:`PriceQuote`.

        SeatGeek nests pricing under ``stats``. ``listing_count == 0``
        is meaningful — it says the URL still resolves but inventory is
        sold out — so we keep the ``0`` and flip ``is_active`` off
        instead of dropping the quote.

        Args:
            payload: A single event object from the SeatGeek response.

        Returns:
            The parsed :class:`PriceQuote`, or ``None`` if the payload
            is missing the fields we'd persist (no URL and no prices).
        """
        stats = payload.get("stats") or {}
        url = payload.get("url")
        listing_count = stats.get("listing_count")
        min_price = stats.get("lowest_price")
        max_price = stats.get("highest_price")
        average_price = stats.get("average_price")

        if not url and min_price is None and max_price is None:
            return None

        is_active = (
            bool(listing_count)
            if listing_count is not None
            else (min_price is not None or max_price is not None)
        )

        return PriceQuote(
            source=self.name,
            min_price=_as_float(min_price),
            max_price=_as_float(max_price),
            average_price=_as_float(average_price),
            listing_count=listing_count if isinstance(listing_count, int) else None,
            currency="USD",
            buy_url=url,
            affiliate_url=None,
            is_active=is_active,
            raw=payload,
        )

    def _venue_id(self, event: Event) -> str | None:
        """Return the SeatGeek venue id for the event's venue, if mapped.

        Args:
            event: The event whose venue we're inspecting.

        Returns:
            The string SeatGeek venue id when ``event.venue.external_ids``
            carries one, ``None`` otherwise.
        """
        venue = getattr(event, "venue", None)
        external_ids = getattr(venue, "external_ids", None) if venue else None
        if not external_ids:
            return None
        sg_id = external_ids.get(self.name)
        return str(sg_id) if sg_id else None

    def _search_query(self, event: Event) -> str | None:
        """Build the ``q`` parameter for an event search.

        Prefers the headliner artist (most distinctive token SeatGeek
        indexes against), falls back to the event title.

        Args:
            event: The event to query for.

        Returns:
            A non-empty query string, or ``None`` when the event has
            neither artists nor a title to search on.
        """
        artists = getattr(event, "artists", None) or []
        if artists:
            headline = next((a for a in artists if a and a.strip()), None)
            if headline:
                return headline.strip()
        title = getattr(event, "title", None)
        if title and title.strip():
            return title.strip()
        return None

    def _get(
        self, url: str, *, params: dict[str, Any]
    ) -> tuple[int, dict[str, Any] | None]:
        """Issue a GET request to SeatGeek with auth params attached.

        404 returns ``(404, None)`` so the caller can distinguish
        "event not on SeatGeek" from "event found but no parseable
        body". 5xx and other 4xx errors raise so the orchestrator
        records a transient failure rather than caching a bad result.

        Args:
            url: Fully qualified URL (no query string).
            params: Query parameters; ``client_id`` and (when set)
                ``client_secret`` are added before sending.

        Returns:
            ``(status_code, payload_or_None)``. Payload is ``None`` on
            a 404 or when the response body fails to parse as JSON.

        Raises:
            requests.RequestException: For network-level errors and
                non-404 4xx/5xx statuses.
        """
        merged: dict[str, Any] = dict(params)
        merged["client_id"] = self.client_id
        if self.client_secret:
            merged["client_secret"] = self.client_secret

        response = requests.get(url, params=merged, timeout=self.timeout)

        if response.status_code == 404:
            return 404, None
        if response.status_code >= 400:
            logger.warning(
                "SeatGeek non-2xx response (%s) on %s",
                response.status_code,
                url,
            )
            response.raise_for_status()

        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            logger.warning("SeatGeek returned non-JSON on %s", url)
            return response.status_code, None
        return response.status_code, data


def _as_float(value: Any) -> float | None:
    """Coerce a SeatGeek numeric field to ``float`` without raising.

    SeatGeek occasionally returns ``0`` for "no listings" and ``None``
    for "no opinion" — the orchestrator treats them identically through
    the snapshot but the UI cares, so we preserve ``None`` rather than
    flatten to ``0.0``.

    Args:
        value: Raw field from the SeatGeek payload.

    Returns:
        ``float(value)`` when ``value`` is a finite number, ``None``
        otherwise.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
