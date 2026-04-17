"""JSON-LD extraction helpers for HTML-based event scrapers.

Many venue websites publish schema.org Event/MusicEvent structured data
in ``<script type="application/ld+json">`` blocks so that Google and
other crawlers can generate rich results. When a venue does, using that
data directly is dramatically more robust than scraping CSS selectors
that break every redesign.

This module centralizes the logic so ``GenericHtmlScraper`` and any
custom venue scraper (e.g. Black Cat) can share it. The only output is
the :class:`RawEvent` dataclass; callers do not need to know anything
about the shape of JSON-LD.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from backend.core.logging import get_logger
from backend.scraper.base.models import RawEvent

logger = get_logger(__name__)

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "Event",
        "MusicEvent",
        "TheaterEvent",
        "ComedyEvent",
        "DanceEvent",
        "Festival",
    }
)


def extract_events(
    html: str,
    *,
    source_url: str,
    venue_external_id: str,
) -> Iterator[RawEvent]:
    """Yield ``RawEvent`` objects from every JSON-LD Event node in ``html``.

    Parses all ``<script type="application/ld+json">`` blocks, flattens
    ``@graph`` arrays, and emits one ``RawEvent`` per node whose
    ``@type`` matches a schema.org Event subtype. Malformed JSON blocks
    are logged and skipped; one bad block never prevents valid blocks
    from being extracted.

    Args:
        html: Fully fetched HTML document.
        source_url: The URL the HTML was fetched from. Used to resolve
            relative URLs inside the JSON-LD and as a fallback event URL.
        venue_external_id: Stable identifier for the venue. Stored on
            each ``RawEvent`` so the runner can associate events with
            the correct venue regardless of the JSON-LD location node.

    Yields:
        RawEvent for each parsed schema.org Event node.
    """
    soup = BeautifulSoup(html, "lxml")
    blocks = soup.find_all("script", attrs={"type": "application/ld+json"})

    for block in blocks:
        payload = block.string or block.get_text() or ""
        if not payload.strip():
            continue

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Skipping malformed JSON-LD block on %s: %s", source_url, exc
            )
            continue

        for node in _flatten(data):
            event = _node_to_raw_event(
                node,
                source_url=source_url,
                venue_external_id=venue_external_id,
            )
            if event is not None:
                yield event


def _flatten(data: Any) -> Iterator[dict[str, Any]]:
    """Walk a JSON-LD payload and yield every Event-shaped node it contains.

    Handles single-object payloads, top-level arrays, and ``@graph``
    containers. Non-Event types are ignored.

    Args:
        data: A parsed JSON-LD document (object, array, or ``@graph``).

    Yields:
        Dict nodes whose ``@type`` indicates a schema.org Event subtype.
    """
    if isinstance(data, list):
        for item in data:
            yield from _flatten(item)
        return

    if not isinstance(data, dict):
        return

    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _flatten(item)

    if _is_event_node(data):
        yield data


def _is_event_node(node: dict[str, Any]) -> bool:
    """Check whether a JSON-LD node represents a schema.org Event.

    Args:
        node: A parsed JSON-LD object.

    Returns:
        True when the node's ``@type`` (string or list) matches a known
        Event subtype.
    """
    raw_type = node.get("@type")
    if isinstance(raw_type, str):
        return raw_type in EVENT_TYPES
    if isinstance(raw_type, list):
        return any(t in EVENT_TYPES for t in raw_type if isinstance(t, str))
    return False


def _node_to_raw_event(
    node: dict[str, Any],
    *,
    source_url: str,
    venue_external_id: str,
) -> RawEvent | None:
    """Convert a single JSON-LD Event node to a ``RawEvent``.

    Returns None if required fields (title, start datetime) are missing
    or unparseable. The node is stored verbatim on ``RawEvent.raw_data``
    so downstream debugging always has the full original payload.

    Args:
        node: A JSON-LD node whose ``@type`` is an Event subtype.
        source_url: The page URL the node was extracted from.
        venue_external_id: Venue identifier to attach to the RawEvent.

    Returns:
        A RawEvent populated from the node, or None if the node lacks
        the minimum required fields.
    """
    title = _coerce_str(node.get("name"))
    starts_at = _parse_datetime(node.get("startDate"))
    if not title or starts_at is None:
        return None

    event_url = _coerce_url(node.get("url"), base=source_url) or source_url
    image_url = _first_image(node.get("image"), base=source_url)
    description = _coerce_str(node.get("description"))
    ends_at = _parse_datetime(node.get("endDate"))
    artists = _extract_artists(node.get("performer"))
    min_price, max_price, ticket_url, on_sale_at = _extract_offer_details(
        node.get("offers"), base=source_url
    )

    external_id = _derive_external_id(
        node,
        event_url=event_url,
        source_url=source_url,
        title=title,
        starts_at=starts_at,
        venue_external_id=venue_external_id,
    )
    raw_data = dict(node)
    raw_data["id"] = external_id

    return RawEvent(
        title=title,
        venue_external_id=venue_external_id,
        starts_at=starts_at,
        source_url=event_url,
        raw_data=raw_data,
        artists=artists,
        description=description,
        ticket_url=ticket_url,
        min_price=min_price,
        max_price=max_price,
        image_url=image_url,
        ends_at=ends_at,
        on_sale_at=on_sale_at,
    )


def _coerce_str(value: Any) -> str | None:
    """Normalize a JSON-LD value to a trimmed string.

    Args:
        value: The raw value from a JSON-LD field.

    Returns:
        The stripped string, or None when the value is empty or not a string.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_url(value: Any, *, base: str) -> str | None:
    """Coerce a value to an absolute URL, resolving against a base if relative.

    Args:
        value: A string, dict with a ``url`` field, or other JSON-LD fragment.
        base: Base URL used to resolve relative paths.

    Returns:
        An absolute URL string, or None when no URL can be derived.
    """
    candidate: str | None = None
    if isinstance(value, str):
        candidate = value
    elif isinstance(value, dict):
        inner = value.get("url") or value.get("@id")
        if isinstance(inner, str):
            candidate = inner

    if not candidate:
        return None
    return urljoin(base, candidate.strip())


def _first_image(value: Any, *, base: str) -> str | None:
    """Return the first image URL from a JSON-LD image field.

    Args:
        value: String, list, or ImageObject dict from the JSON-LD node.
        base: Base URL used to resolve relative image paths.

    Returns:
        An absolute image URL, or None when no image is present.
    """
    if isinstance(value, list):
        for item in value:
            url = _coerce_url(item, base=base)
            if url:
                return url
        return None
    return _coerce_url(value, base=base)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 datetime string from JSON-LD into a ``datetime``.

    Accepts both date-only (``2026-05-01``) and full ISO-8601 (including
    trailing ``Z`` for UTC). Returns None when the value cannot be parsed.

    Args:
        value: The raw JSON-LD date/datetime string.

    Returns:
        A naive or aware ``datetime`` matching what the source provided,
        or None when parsing fails.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(f"{text}T00:00:00")
    except ValueError:
        logger.debug("Could not parse datetime value '%s'.", text)
        return None


def _extract_artists(value: Any) -> list[str]:
    """Extract performer names from a JSON-LD ``performer`` field.

    The field may be a single object, a list of objects, or a plain
    string. Unknown shapes are ignored rather than crashing extraction.

    Args:
        value: The raw ``performer`` value from the JSON-LD node.

    Returns:
        A de-duplicated list of performer names in the original order.
    """
    items: list[Any]
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]

    seen: set[str] = set()
    names: list[str] = []
    for item in items:
        name: str | None = None
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = _coerce_str(item.get("name"))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _extract_offer_details(
    value: Any,
    *,
    base: str,
) -> tuple[float | None, float | None, str | None, datetime | None]:
    """Pull price, ticket URL, and on-sale date from a JSON-LD offers field.

    ``offers`` is one of the most inconsistent fields in schema.org — it
    may be a single Offer, a list of Offers, or absent entirely. This
    helper scans every offer it finds, keeps the min/max numeric price,
    returns the first usable ticket URL, and keeps the earliest
    ``validFrom`` on-sale date.

    Args:
        value: The raw ``offers`` value from the JSON-LD node.
        base: Base URL for resolving relative ticket URLs.

    Returns:
        Tuple of (min_price, max_price, ticket_url, on_sale_at). Any
        field that cannot be determined is returned as None.
    """
    offers: list[Any]
    if isinstance(value, list):
        offers = value
    elif value is None:
        offers = []
    else:
        offers = [value]

    min_price: float | None = None
    max_price: float | None = None
    ticket_url: str | None = None
    on_sale_at: datetime | None = None

    for offer in offers:
        if not isinstance(offer, dict):
            continue

        price = _coerce_float(offer.get("price"))
        low = _coerce_float(offer.get("lowPrice"))
        high = _coerce_float(offer.get("highPrice"))

        for candidate in (price, low):
            if candidate is not None:
                min_price = candidate if min_price is None else min(min_price, candidate)

        for candidate in (price, high):
            if candidate is not None:
                max_price = candidate if max_price is None else max(max_price, candidate)

        if ticket_url is None:
            ticket_url = _coerce_url(offer.get("url"), base=base)

        offer_on_sale = _parse_datetime(offer.get("validFrom"))
        if offer_on_sale is not None:
            if on_sale_at is None or offer_on_sale < on_sale_at:
                on_sale_at = offer_on_sale

    return min_price, max_price, ticket_url, on_sale_at


def _coerce_float(value: Any) -> float | None:
    """Convert a JSON-LD numeric field (string or number) to a float.

    Args:
        value: The raw value from a price-style JSON-LD field.

    Returns:
        The parsed float, or None when the value is missing/unparseable.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace("$", "").replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _derive_external_id(
    node: dict[str, Any],
    *,
    event_url: str,
    source_url: str,
    title: str,
    starts_at: datetime,
    venue_external_id: str,
) -> str:
    """Compute a stable external ID for an event extracted from JSON-LD.

    Preference order:

    1. An explicit ``@id`` or ``identifier`` on the node.
    2. The event's own detail URL — but only when it differs from the
       page source URL. Some venues (e.g. Flash DC) emit JSON-LD events
       with no per-event ``url`` field, in which case the extractor
       falls back to the page URL for display purposes. Using that as
       the external_id would collapse every event on the page into one
       dedup bucket.
    3. A SHA-256 fingerprint of venue + title + start time — stable
       across runs so the runner can still dedup, but unique per event.

    Args:
        node: The JSON-LD node the event was extracted from.
        event_url: Resolved absolute URL for the event detail page.
        source_url: Page URL the JSON-LD was scraped from. Used to
            detect the "event_url fell back to the page URL" case.
        title: Event title used as hash input when no URL-based ID exists.
        starts_at: Event start datetime. Included in the fingerprint so
            distinct dates of the same title-at-venue don't collide.
        venue_external_id: Venue identifier, included in the fingerprint
            to prevent cross-venue collisions.

    Returns:
        A stable external ID string.
    """
    explicit = node.get("@id") or node.get("identifier")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if event_url and event_url != source_url:
        return event_url

    fingerprint = f"{venue_external_id}|{title}|{starts_at.isoformat()}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return f"jsonld:{digest}"
