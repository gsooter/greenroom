"""Dice.fm venue scraper using JSON-LD structured data extraction.

Dice.fm embeds full event data in the raw HTML of every venue page, so
this scraper does not need a headless browser. It fetches the page with
a standard browser User-Agent, parses the embedded structured data, and
yields one ``RawEvent`` per show.

JSON-LD is the primary source because it is explicitly designed for
machine consumption and Dice has a strong SEO incentive to keep it
accurate. ``__NEXT_DATA__`` is a fallback: if Dice ever removes the
JSON-LD, the Next.js bootstrap payload still carries the same events
under a vendor-specific shape, which keeps the scraper alive through
one more redesign cycle.

If neither source yields events the scraper raises
:class:`DiceScraperError` so the runner marks the run FAILED and the
validator alerts within one nightly cycle.

Investigation findings (captured against live pages on 2026-04-24,
User-Agent: Chrome/122 desktop, no JavaScript executed):

1. JSON-LD is present in the raw HTML — no client-side rendering is
   required. Each venue page carries four
   ``<script type="application/ld+json">`` blocks:

   - block #0: a ``Place`` node with a ``name``, ``address``, ``geo``
     (``latitude``/``longitude``), and an ``event`` array containing
     every upcoming event at the venue (30 events on DC9, Songbyrd,
     and BERHTA; 2 on Byrdland at the time of inspection).
   - block #1: a ``Brand`` node for DICE itself — not useful.
   - block #2, #3: ``WebSite`` nodes — not useful.

   Only block #0 matters for scraping.

2. Each event inside ``Place.event`` is a self-contained schema.org
   ``Event`` node with fields we care about:

   - ``@type`` = ``"Event"``
   - ``name`` — event title (e.g. "Nerd Nite")
   - ``url`` — absolute Dice event URL, acts as a stable external id
   - ``startDate`` — ISO 8601 with timezone offset, e.g.
     ``"2026-04-24T20:00:00-04:00"`` (already America/New_York-aware,
     does NOT need localization)
   - ``endDate`` — same format; optional
   - ``image`` — list of URLs (usually one)
   - ``description`` — free text, often includes doors time and age
     restrictions
   - ``location`` — nested ``Place`` with name, address, geo
   - ``offers`` — often an empty list on browse listings; occasionally
     populated with ``Offer`` objects carrying ``price``/``lowPrice``
     and ``url``
   - ``performer`` — typically absent on browse listings; __NEXT_DATA__
     carries the richer ``summary_lineup`` block when we need it
   - ``organizer`` — ``{"@type": "Organization", "name": "..."}``

3. ``__NEXT_DATA__`` is also present under
   ``<script id="__NEXT_DATA__">``. The event array lives at
   ``props.pageProps.profile.sections[*].events`` — the sections are
   usually "Upcoming" (index 0), but we traverse all of them to be
   safe against future section splits ("This week", "Next month").
   Each event has native Dice fields:

   - ``perm_name`` — Dice's stable event slug, forms the URL:
     ``https://dice.fm/event/{perm_name}``
   - ``name``, ``images.landscape``/``portrait``/``square``
   - ``dates.event_start_date``, ``dates.event_end_date``,
     ``dates.timezone`` (IANA), ``venues[0].doors_open_date``
   - ``price.amount`` (in *cents*, USD), ``price.currency``
   - ``summary_lineup.top_artists`` — list of ``{name, artist_id,
     is_headliner, image}`` objects; full lineup breakdown
   - ``tags_types`` — list of ``{name, value, title}`` genre-ish tags

4. JavaScript rendering is NOT required. The initial HTML response
   (status 200, ~566 KB on DC9) contains every field above.

Field mapping (primary, JSON-LD):

  RawEvent.title               ← event.name
  RawEvent.venue_external_id   ← constructor ``venue_external_id``
  RawEvent.starts_at           ← parse(event.startDate)  (tz-aware)
  RawEvent.ends_at             ← parse(event.endDate)    (optional)
  RawEvent.source_url          ← event.url
  RawEvent.ticket_url          ← event.url (Dice event URL is the
                                  ticket URL on Dice)
  RawEvent.image_url           ← event.image[0]
  RawEvent.description         ← event.description
  RawEvent.artists             ← performer names when present, else
                                  [event.name]
  RawEvent.min_price           ← offers[].price or offers[].lowPrice
  RawEvent.max_price           ← offers[].price or offers[].highPrice
  RawEvent.on_sale_at          ← offers[].validFrom
  RawEvent.raw_data            ← the full JSON-LD event node verbatim

Field mapping (fallback, __NEXT_DATA__):

  RawEvent.title               ← event.name
  RawEvent.venue_external_id   ← constructor ``venue_external_id``
  RawEvent.starts_at           ← parse(event.dates.event_start_date)
  RawEvent.ends_at             ← parse(event.dates.event_end_date)
  RawEvent.source_url          ← "https://dice.fm/event/" + perm_name
  RawEvent.ticket_url          ← same as source_url
  RawEvent.image_url           ← event.images.landscape (fall back to
                                  square, portrait)
  RawEvent.description         ← event.about.description (if present)
  RawEvent.artists             ← [a.name for a in summary_lineup
                                  .top_artists] or [event.name]
  RawEvent.min_price           ← event.price.amount / 100.0
  RawEvent.max_price           ← event.price.amount / 100.0
  RawEvent.raw_data            ← the full NEXT_DATA event node

DC venues using this scraper:

  dc9       https://dice.fm/venue/dc9-q2xvo
  berhta    https://dice.fm/venue/berhta-8emn5
  songbyrd  https://dice.fm/venue/songbyrd-r58r
  byrdland  https://dice.fm/venue/byrdland-wo3n
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from backend.core.logging import get_logger
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT_SECONDS = 30.0
INTER_REQUEST_DELAY_SECONDS = 2.0
CONNECTION_RETRY_BACKOFF_SECONDS = 5.0
EVENT_BASE_URL = "https://dice.fm/event/"

_EVENT_TYPES = frozenset({"Event", "MusicEvent"})


class DiceScraperError(Exception):
    """Raised when the Dice venue page yields no usable event data.

    Covers two failure modes: the HTTP fetch itself failed (non-200
    response or connection error after retry) and the page returned
    200 but neither JSON-LD nor ``__NEXT_DATA__`` produced any events.
    Either way the runner should mark the run FAILED and let the
    validator alert.
    """


class DiceScraper(BaseScraper):
    """Scrape a single Dice.fm venue page and yield one RawEvent per show.

    Reuses one ``requests.Session`` per instance so multi-venue nightly
    runs get connection pooling. Between scrapes the caller should
    construct a fresh :class:`DiceScraper` per venue — the runner's
    per-venue instantiation already handles that.

    Attributes:
        venue_external_id: Stable venue identifier stored on each
            yielded ``RawEvent``. For the GREENROOM runner this is the
            venue's slug in the database.
        dice_venue_url: Fully qualified Dice.fm venue URL to scrape,
            e.g. ``https://dice.fm/venue/dc9-q2xvo``.
    """

    source_platform = "dice"

    def __init__(
        self,
        *,
        venue_external_id: str,
        dice_venue_url: str,
    ) -> None:
        """Initialize the scraper for a specific Dice.fm venue.

        Args:
            venue_external_id: The GREENROOM venue slug this scraper is
                for. Used to populate ``RawEvent.venue_external_id`` on
                every yielded event so the runner can route them to the
                correct database venue.
            dice_venue_url: Full Dice.fm venue URL to scrape.
                Example: ``https://dice.fm/venue/dc9-q2xvo``.
        """
        self.venue_external_id = venue_external_id
        self.dice_venue_url = dice_venue_url
        self._session = requests.Session()
        self._last_request_at: float | None = None

    def scrape(self) -> Iterator[RawEvent]:
        """Fetch the configured Dice.fm venue page and yield its events.

        Yields:
            RawEvent for every parseable event on the page, sourced
            from JSON-LD when available, otherwise from __NEXT_DATA__.

        Raises:
            DiceScraperError: If the page cannot be fetched or neither
                data source yields any events.
        """
        logger.info(
            "DiceScraper fetching %s (venue=%s)",
            self.dice_venue_url,
            self.venue_external_id,
        )
        html = self._fetch(self.dice_venue_url)

        yielded = 0
        for event in self._parse_jsonld(html):
            yielded += 1
            yield event

        if yielded == 0:
            logger.warning(
                "Dice JSON-LD yielded 0 events for %s — falling back to __NEXT_DATA__.",
                self.dice_venue_url,
            )
            for event in self._parse_next_data(html):
                yielded += 1
                yield event

        if yielded == 0:
            raise DiceScraperError(
                f"Dice venue page {self.dice_venue_url} produced no events "
                "from either JSON-LD or __NEXT_DATA__."
            )

        logger.info(
            "DiceScraper yielded %d events from %s.", yielded, self.dice_venue_url
        )

    # ------------------------------------------------------------------ HTTP

    def _fetch(self, url: str) -> str:
        """Fetch a Dice.fm page, respecting rate limits and retrying once.

        Applies a 2-second pause between requests on the same session so
        multi-venue runs don't hammer Dice, retries once on connection
        errors with a 5-second backoff, and raises DiceScraperError on
        non-200 responses or repeated connection failures.

        Args:
            url: Fully qualified Dice.fm URL to fetch.

        Returns:
            The response body as a string.

        Raises:
            DiceScraperError: If the page cannot be fetched successfully.
        """
        self._respect_rate_limit()

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            response = self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.ConnectionError as exc:
            logger.warning(
                "Dice connection error on %s: %s — retrying once after %.0fs",
                url,
                exc,
                CONNECTION_RETRY_BACKOFF_SECONDS,
            )
            time.sleep(CONNECTION_RETRY_BACKOFF_SECONDS)
            try:
                response = self._session.get(
                    url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
                )
            except requests.ConnectionError as exc2:
                raise DiceScraperError(
                    f"Dice fetch failed for {url} after one retry: {exc2}"
                ) from exc2
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code != 200:
            logger.error("Dice returned HTTP %d for %s.", response.status_code, url)
            raise DiceScraperError(
                f"Dice returned HTTP {response.status_code} for {url}"
            )

        return response.text

    def _respect_rate_limit(self) -> None:
        """Sleep just long enough that consecutive requests are ≥ 2s apart.

        A scraper run that hits multiple Dice venues in sequence shares
        one session, so we pace ourselves here rather than relying on
        the runner.
        """
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = INTER_REQUEST_DELAY_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)

    # ------------------------------------------------------------- Parsing

    def _parse_jsonld(self, html: str) -> Iterator[RawEvent]:
        """Extract RawEvents from every JSON-LD Event node in the HTML.

        Walks every ``<script type="application/ld+json">`` tag on the
        page, including events that live inside a ``Place.event``
        array (Dice's usual shape) as well as bare top-level Event
        nodes or ``@graph`` arrays. Malformed JSON blocks and malformed
        individual events are logged and skipped so one bad payload
        never crashes the whole scrape.

        Args:
            html: The full HTML body fetched from the Dice venue page.

        Yields:
            RawEvent per successfully-parsed JSON-LD Event node.
        """
        soup = BeautifulSoup(html, "lxml")
        blocks = soup.find_all("script", attrs={"type": "application/ld+json"})

        for idx, block in enumerate(blocks):
            payload = block.string or block.get_text() or ""
            payload = payload.strip()
            if not payload:
                continue

            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Skipping malformed JSON-LD block #%d on %s: %s",
                    idx,
                    self.dice_venue_url,
                    exc,
                )
                continue

            for node in _iter_event_nodes(data):
                try:
                    event = self._jsonld_node_to_raw_event(node)
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed Dice JSON-LD event on %s: %s",
                        self.dice_venue_url,
                        exc,
                    )
                    continue
                if event is not None:
                    yield event

    def _parse_next_data(self, html: str) -> Iterator[RawEvent]:
        """Fallback: extract events from the ``__NEXT_DATA__`` bootstrap payload.

        Used only when JSON-LD produces zero events. Navigates
        ``props.pageProps.profile.sections[*].events`` and maps each
        Dice-native event object to a ``RawEvent``.

        Args:
            html: The full HTML body fetched from the Dice venue page.

        Yields:
            RawEvent per event found in the __NEXT_DATA__ payload.
        """
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag is None:
            logger.warning(
                "No __NEXT_DATA__ tag on %s — nothing to fall back to.",
                self.dice_venue_url,
            )
            return

        payload = tag.string or tag.get_text() or ""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning(
                "__NEXT_DATA__ on %s is not valid JSON: %s",
                self.dice_venue_url,
                exc,
            )
            return

        sections = (
            data.get("props", {})
            .get("pageProps", {})
            .get("profile", {})
            .get("sections", [])
        )
        if not isinstance(sections, list):
            return

        for section in sections:
            if not isinstance(section, dict):
                continue
            events = section.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                try:
                    raw = self._next_data_event_to_raw_event(event)
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed __NEXT_DATA__ event on %s: %s",
                        self.dice_venue_url,
                        exc,
                    )
                    continue
                if raw is not None:
                    yield raw

    # -------------------------------------------------- node → RawEvent

    def _jsonld_node_to_raw_event(self, node: dict[str, Any]) -> RawEvent | None:
        """Map a JSON-LD ``Event`` node to a :class:`RawEvent`.

        Returns None when the node is missing required fields (title or
        start datetime). ``raw_data`` preserves the full original node
        so downstream debugging always has the source payload.

        Args:
            node: The JSON-LD Event node.

        Returns:
            A fully populated RawEvent, or None if required fields are
            missing.
        """
        title = _coerce_str(node.get("name"))
        starts_at = _parse_datetime(node.get("startDate"))
        if not title or starts_at is None:
            return None

        event_url = _coerce_url(node.get("url"), base=self.dice_venue_url)
        source_url = event_url or self.dice_venue_url
        image_url = _first_image(node.get("image"), base=self.dice_venue_url)
        description = _coerce_str(node.get("description"))
        ends_at = _parse_datetime(node.get("endDate"))
        artists = _extract_artists(node.get("performer")) or [title]
        min_price, max_price, offer_ticket_url, on_sale_at = _extract_offer_details(
            node.get("offers"), base=self.dice_venue_url
        )

        raw_data = dict(node)
        # Preserve a stable id so the runner's _extract_external_id picks
        # it up even when the Dice node uses @id instead of id.
        if "id" not in raw_data and "@id" in raw_data:
            raw_data["id"] = raw_data["@id"]
        elif "id" not in raw_data and event_url:
            raw_data["id"] = event_url

        return RawEvent(
            title=title,
            venue_external_id=self.venue_external_id,
            starts_at=starts_at,
            source_url=source_url,
            raw_data=raw_data,
            artists=artists,
            description=description,
            ticket_url=offer_ticket_url or event_url,
            min_price=min_price,
            max_price=max_price,
            image_url=image_url,
            ends_at=ends_at,
            on_sale_at=on_sale_at,
        )

    def _next_data_event_to_raw_event(self, node: dict[str, Any]) -> RawEvent | None:
        """Map a Dice ``__NEXT_DATA__`` event object to a :class:`RawEvent`.

        Args:
            node: A single event object from
                ``props.pageProps.profile.sections[*].events``.

        Returns:
            A fully populated RawEvent, or None if required fields are
            missing.
        """
        title = _coerce_str(node.get("name"))
        dates = node.get("dates") or {}
        starts_at = _parse_datetime(dates.get("event_start_date"))
        if not title or starts_at is None:
            return None

        perm_name = _coerce_str(node.get("perm_name"))
        event_url = f"{EVENT_BASE_URL}{perm_name}" if perm_name else self.dice_venue_url

        images = node.get("images") or {}
        image_url: str | None = None
        for key in ("landscape", "square", "portrait"):
            candidate = images.get(key) if isinstance(images, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                image_url = candidate.strip()
                break

        description: str | None = None
        about = node.get("about")
        if isinstance(about, dict):
            description = _coerce_str(about.get("description"))

        artists: list[str] = []
        lineup = node.get("summary_lineup") or {}
        top_artists = lineup.get("top_artists") if isinstance(lineup, dict) else None
        if isinstance(top_artists, list):
            for artist in top_artists:
                if isinstance(artist, dict):
                    name = _coerce_str(artist.get("name"))
                    if name and name not in artists:
                        artists.append(name)
        if not artists:
            artists = [title]

        min_price: float | None = None
        max_price: float | None = None
        price = node.get("price")
        if isinstance(price, dict):
            amount = price.get("amount")
            if isinstance(amount, int | float) and not isinstance(amount, bool):
                dollars = float(amount) / 100.0
                min_price = dollars
                max_price = dollars

        ends_at = _parse_datetime(dates.get("event_end_date"))

        raw_data = dict(node)
        if "id" not in raw_data and perm_name:
            raw_data["id"] = f"dice:{perm_name}"

        return RawEvent(
            title=title,
            venue_external_id=self.venue_external_id,
            starts_at=starts_at,
            source_url=event_url,
            raw_data=raw_data,
            artists=artists,
            description=description,
            ticket_url=event_url,
            min_price=min_price,
            max_price=max_price,
            image_url=image_url,
            ends_at=ends_at,
        )


# ======================================================= Module helpers


def _iter_event_nodes(data: Any) -> Iterator[dict[str, Any]]:
    """Yield every schema.org Event node reachable from ``data``.

    Handles four shapes that appear in the wild:

    1. A top-level Event or MusicEvent object.
    2. A top-level array containing Event objects.
    3. An ``@graph`` wrapper around an array of nodes.
    4. A ``Place`` (or ``MusicVenue``) object whose ``event`` property is
       an array of Event nodes — this is the Dice.fm shape.

    Non-Event types are skipped. The walk is depth-bounded implicitly
    because Place nodes are a single level deep.

    Args:
        data: Parsed JSON value from a JSON-LD script block.

    Yields:
        Dicts whose ``@type`` marks them as a schema.org Event subtype.
    """
    if isinstance(data, list):
        for item in data:
            yield from _iter_event_nodes(item)
        return

    if not isinstance(data, dict):
        return

    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_event_nodes(item)

    events = data.get("event")
    if isinstance(events, list):
        for item in events:
            yield from _iter_event_nodes(item)

    if _is_event_type(data.get("@type")):
        yield data


def _is_event_type(raw_type: Any) -> bool:
    """Return True when a JSON-LD ``@type`` names a schema.org Event subtype.

    Args:
        raw_type: The raw value of the ``@type`` field — may be a string,
            a list of strings, or absent.

    Returns:
        True when any entry in ``raw_type`` falls in
        :data:`_EVENT_TYPES`.
    """
    if isinstance(raw_type, str):
        return raw_type in _EVENT_TYPES
    if isinstance(raw_type, list):
        return any(t in _EVENT_TYPES for t in raw_type if isinstance(t, str))
    return False


def _coerce_str(value: Any) -> str | None:
    """Normalize a JSON value to a stripped string.

    Args:
        value: Any JSON value.

    Returns:
        The stripped string, or None if ``value`` is not a non-empty
        string.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_url(value: Any, *, base: str) -> str | None:
    """Coerce a JSON-LD value into an absolute URL.

    Args:
        value: A string, an object with a ``url`` / ``@id`` field, or
            any other JSON-LD fragment.
        base: Base URL used to resolve relative paths.

    Returns:
        An absolute URL string, or None if no URL can be derived.
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
    """Return the first resolvable image URL from a JSON-LD ``image`` field.

    Args:
        value: String, list, or ImageObject dict from the JSON-LD node.
        base: Base URL used to resolve relative paths.

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
    """Parse an ISO-8601 string (``Z`` or offset) into a ``datetime``.

    Dice's JSON-LD ``startDate`` and ``__NEXT_DATA__`` ``event_start_date``
    are both ISO-8601 with an explicit offset (``-04:00`` during DST,
    ``-05:00`` otherwise), so the parsed datetime is always timezone-
    aware. Returns None on any parse failure.

    Args:
        value: The raw date/datetime value from Dice.

    Returns:
        A ``datetime``, tz-aware when the input carried an offset,
        or None when the value cannot be parsed.
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
        return None


def _extract_artists(value: Any) -> list[str]:
    """Pull performer names out of a JSON-LD ``performer`` field.

    Accepts a single object, a list of objects, or a plain string.
    Ignores unknown shapes rather than raising.

    Args:
        value: The raw ``performer`` value from the JSON-LD node.

    Returns:
        De-duplicated list of performer names in original order.
    """
    if value is None:
        return []
    items: list[Any] = value if isinstance(value, list) else [value]

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
    """Read price, ticket URL, and on-sale date from a JSON-LD ``offers`` field.

    Args:
        value: A single Offer, a list of Offers, or None.
        base: Base URL for resolving relative ticket URLs.

    Returns:
        (min_price, max_price, ticket_url, on_sale_at). Any unavailable
        field is returned as None.
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
                min_price = (
                    candidate if min_price is None else min(min_price, candidate)
                )
        for candidate in (price, high):
            if candidate is not None:
                max_price = (
                    candidate if max_price is None else max(max_price, candidate)
                )

        if ticket_url is None:
            ticket_url = _coerce_url(offer.get("url"), base=base)

        offer_on_sale = _parse_datetime(offer.get("validFrom"))
        if offer_on_sale is not None and (
            on_sale_at is None or offer_on_sale < on_sale_at
        ):
            on_sale_at = offer_on_sale

    return min_price, max_price, ticket_url, on_sale_at


def _coerce_float(value: Any) -> float | None:
    """Convert a price-style value to float.

    Accepts numeric types and strings like ``"$25.00"`` or ``"25,00"``
    (the latter via simple comma removal; Dice uses ``.`` so it's a
    defensive no-op here).

    Args:
        value: The raw JSON value.

    Returns:
        The parsed float, or None when the input is missing or not
        parseable.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
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
