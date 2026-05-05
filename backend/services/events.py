"""Event business logic — search, filtering, and feed generation.

All event-related business logic lives here. API routes call these
functions and never access the repository layer directly.
"""

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError, ValidationError
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.repositories import artists as artists_repo
from backend.data.repositories import events as events_repo
from backend.data.repositories import follows as follows_repo

_ET_ZONE = ZoneInfo("America/New_York")
_DEFAULT_TZ_NAME = "America/New_York"

NearMeWindow = Literal["tonight", "week"]
_NEAR_ME_WINDOWS: frozenset[str] = frozenset({"tonight", "week"})
_EARTH_RADIUS_KM = 6371.0088


class TodayWindow:
    """A UTC-aware ``[start, end)`` interval representing one local calendar day.

    ``start`` is the UTC instant of local midnight at the head of the
    requested day; ``end`` is the UTC instant of the next local midnight.
    Half-open at the end so events at exactly the next local midnight
    fall into the following day, matching wall-clock intuition.
    """

    __slots__ = ("end", "start")

    def __init__(self, start: datetime, end: datetime) -> None:
        """Build a TodayWindow.

        Args:
            start: UTC datetime of local midnight at the day's head.
            end: UTC datetime of the next local midnight (exclusive).
        """
        self.start = start
        self.end = end

    def __repr__(self) -> str:
        """Return a debug-friendly string representation.

        Returns:
            String describing the window's UTC bounds.
        """
        return (
            f"TodayWindow(start={self.start.isoformat()}, end={self.end.isoformat()})"
        )


def compute_today_utc_window(
    *,
    timezone_name: str | None = None,
    now_utc: datetime | None = None,
) -> TodayWindow:
    """Compute the UTC bounds of "today" in the requested timezone.

    Args:
        timezone_name: IANA timezone name (e.g. ``"America/New_York"``).
            None falls back to America/New_York — the DMV default.
        now_utc: Clock anchor. Defaults to ``datetime.now(UTC)``; tests
            inject a fixed instant to exercise day-boundary behavior.

    Returns:
        A :class:`TodayWindow` whose ``[start, end)`` half-open interval
        is exactly 24 hours in wall-clock terms (with DST exceptions).

    Raises:
        ValidationError: If ``timezone_name`` does not resolve to a real
            IANA zone.
    """
    name = timezone_name or _DEFAULT_TZ_NAME
    try:
        zone = ZoneInfo(name)
    except Exception as exc:  # ZoneInfoNotFoundError is the typical case
        raise ValidationError(f"Unknown timezone: '{name}'") from exc
    anchor = (now_utc or datetime.now(UTC)).astimezone(zone)
    today_start_local = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start_local = today_start_local + timedelta(days=1)
    return TodayWindow(
        start=today_start_local.astimezone(UTC),
        end=tomorrow_start_local.astimezone(UTC),
    )


def get_event(session: Session, event_id: uuid.UUID) -> Event:
    """Fetch a single event by ID.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.

    Returns:
        The Event instance.

    Raises:
        NotFoundError: If the event does not exist.
    """
    event = events_repo.get_event_by_id(session, event_id)
    if event is None:
        raise NotFoundError(
            code=EVENT_NOT_FOUND,
            message=f"No event found with id {event_id}",
        )
    return event


def get_event_by_slug(session: Session, slug: str) -> Event:
    """Fetch a single event by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Event instance.

    Raises:
        NotFoundError: If the event does not exist.
    """
    event = events_repo.get_event_by_slug(session, slug)
    if event is None:
        raise NotFoundError(
            code=EVENT_NOT_FOUND,
            message=f"No event found with slug '{slug}'",
        )
    return event


_VALID_SORTS: frozenset[str] = frozenset({"date", "for_you"})
_VALID_TIME_OF_DAY: frozenset[str] = frozenset({"early", "evening", "late"})


def list_events(
    session: Session,
    *,
    city_id: uuid.UUID | None = None,
    region: str | None = None,
    venue_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    today: bool = False,
    timezone_name: str | None = None,
    now_utc: datetime | None = None,
    genres: list[str] | None = None,
    artist_ids: list[uuid.UUID] | None = None,
    artist_search: str | None = None,
    price_max: float | None = None,
    free_only: bool = False,
    available_only: bool = False,
    event_type: str | None = None,
    status: str | None = None,
    sort: str | None = None,
    user_id: uuid.UUID | None = None,
    day_of_week: list[int] | None = None,
    time_of_day: list[str] | None = None,
    has_image: bool = False,
    has_price: bool = False,
    followed_venues_only: bool = False,
    followed_artists_only: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """List events with optional filters and pagination.

    Args:
        session: Active SQLAlchemy session.
        city_id: Filter to events in a specific city.
        region: Filter to events in cities in this region (e.g., "DMV").
        venue_ids: Filter to specific venues.
        date_from: Start of date range. When set, takes precedence over
            ``today`` so an explicit historical query is never overridden.
        date_to: End of date range.
        today: When True and ``date_from`` is None, narrows the result
            to events whose ``starts_at`` falls within the caller's
            local calendar day (computed in ``timezone_name``). Resolves
            the day-boundary timezone bug — see Fix #1 in the bug-fix
            sprint.
        timezone_name: IANA timezone for the ``today`` window. None
            falls back to ``America/New_York`` (the DMV default) so
            anonymous DMV callers still get the correct boundary.
        now_utc: Clock anchor for the ``today`` window. Tests inject a
            fixed instant; production defaults to ``datetime.now(UTC)``.
        genres: Filter by genre overlap.
        artist_ids: Filter to events whose Spotify artist IDs overlap
            those of the supplied :class:`Artist` UUIDs. Artists without
            a resolved ``spotify_id`` are silently skipped — once
            enrichment lands they'll start matching automatically. If
            none of the supplied artists have a Spotify ID, the result
            set is empty (the caller asked for "shows by these artists"
            and we can't answer that yet).
        artist_search: Case-insensitive substring on ``events.artists``.
        price_max: Upper bound (inclusive) for ``min_price``. Must be
            non-negative when supplied.
        free_only: Restrict to free shows. When True, ``price_max`` is
            ignored.
        available_only: Hide cancelled/sold-out/past shows.
        event_type: Filter by event type string.
        status: Filter by event status string. Takes precedence over
            ``available_only`` when both are passed.
        sort: Sort key. ``"for_you"`` orders by the user's persisted
            recommendation scores descending — requires ``user_id`` and
            silently degrades to ``"date"`` for anonymous callers so the
            public path stays usable. Default and ``"date"`` order by
            ``starts_at`` ascending.
        user_id: Greenroom user UUID. Required for ``followed_venues_only``,
            ``followed_artists_only``, and ``sort='for_you'``.
        day_of_week: Filter to specific weekdays (0=Sun..6=Sat). Each
            value must be in 0..6 or a ValidationError is raised. ``None``
            and ``[]`` skip the filter.
        time_of_day: Subset of ``{"early", "evening", "late"}``. Unknown
            values raise a ValidationError so the caller surfaces the bug.
        has_image: When True, only return events with an image_url set.
        has_price: When True, only return events with min_price set.
        followed_venues_only: When True, restrict to events at venues the
            caller follows. Silently degrades to "no filter" for anonymous
            callers — pairs with the public listing's anonymous fallback.
        followed_artists_only: When True, restrict to events whose
            ``spotify_artist_ids`` overlap one of the caller's followed
            (and Spotify-enriched) artists. Anonymous callers get no
            filter; users with no enriched follows get an empty result.
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (events list, total count).

    Raises:
        ValidationError: If ``per_page`` exceeds 100, ``price_max`` is
            negative, an enum string is unrecognized, ``sort`` is not a
            supported value, ``day_of_week`` contains an out-of-range
            integer, or ``time_of_day`` contains an unknown bucket.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    if price_max is not None and price_max < 0:
        raise ValidationError("price_max cannot be negative.")

    if sort is not None and sort not in _VALID_SORTS:
        raise ValidationError(
            f"Invalid sort: '{sort}'. Valid values: {sorted(_VALID_SORTS)}"
        )

    if day_of_week is not None:
        for d in day_of_week:
            if d < 0 or d > 6:
                raise ValidationError(
                    f"Invalid day_of_week: {d}. Must be 0..6 (0=Sunday)."
                )

    if time_of_day is not None:
        invalid = [b for b in time_of_day if b not in _VALID_TIME_OF_DAY]
        if invalid:
            raise ValidationError(
                f"Invalid time_of_day buckets: {invalid}. "
                f"Valid values: {sorted(_VALID_TIME_OF_DAY)}"
            )

    parsed_type: EventType | None = None
    if event_type is not None:
        try:
            parsed_type = EventType(event_type.lower())
        except ValueError as err:
            raise ValidationError(
                f"Invalid event_type: '{event_type}'. "
                f"Valid values: {[e.value for e in EventType]}"
            ) from err

    parsed_status: EventStatus | None = None
    if status is not None:
        try:
            parsed_status = EventStatus(status.lower())
        except ValueError as err:
            raise ValidationError(
                f"Invalid status: '{status}'. "
                f"Valid values: {[s.value for s in EventStatus]}"
            ) from err

    spotify_ids: list[str] | None = None
    if artist_ids is not None:
        artists = artists_repo.list_artists_by_ids(session, artist_ids)
        spotify_ids = [a.spotify_id for a in artists if a.spotify_id]

    # Compose follow-based restrictions on top of the explicit filters.
    # An anonymous caller (user_id=None) silently drops the toggle so the
    # public listing isn't taken hostage by a stale URL.
    effective_venue_ids = venue_ids
    if followed_venues_only and user_id is not None:
        followed = follows_repo.list_followed_venue_ids(session, user_id)
        followed_list = sorted(followed)
        if effective_venue_ids is not None:
            effective_venue_ids = [v for v in effective_venue_ids if v in followed]
        else:
            effective_venue_ids = followed_list
        # Empty list means "user follows nothing" — short-circuit to no
        # rows by passing an empty list through to the repo's venue_ids
        # filter, which `.in_([])` will reduce to no matches.
        if not effective_venue_ids:
            effective_venue_ids = []

    effective_spotify_ids = spotify_ids
    if followed_artists_only and user_id is not None:
        signals = follows_repo.list_followed_artist_signals(session, user_id)
        followed_spotify = sorted(signals.get("spotify_ids", {}).keys())
        if effective_spotify_ids is not None:
            keep = set(followed_spotify)
            effective_spotify_ids = [s for s in effective_spotify_ids if s in keep]
        else:
            effective_spotify_ids = followed_spotify
        # Empty list semantics already handled by the repo: it short-
        # circuits to zero results, which is correct here too — "shows
        # by my followed artists" with no enriched follows = nothing.

    # Anonymous "for_you" requests degrade to date order — silently, so
    # the public listing keeps working when an unauthenticated client
    # sends `?sort=for_you` from a cached link.
    effective_sort = sort
    if effective_sort == "for_you" and user_id is None:
        effective_sort = "date"

    # Resolve the timezone-aware today window once. Explicit ``date_from``
    # always wins so a historical query (``date_from=2025-01-01``) is
    # never silently clipped by the today flag.
    starts_at_ge: datetime | None = None
    starts_at_lt: datetime | None = None
    if today and date_from is None:
        window = compute_today_utc_window(timezone_name=timezone_name, now_utc=now_utc)
        starts_at_ge = window.start
        starts_at_lt = window.end

    return events_repo.list_events(
        session,
        city_id=city_id,
        region=region,
        venue_ids=effective_venue_ids,
        date_from=date_from,
        date_to=date_to,
        starts_at_ge=starts_at_ge,
        starts_at_lt=starts_at_lt,
        genres=genres,
        spotify_artist_ids=effective_spotify_ids,
        artist_search=artist_search,
        price_max=price_max,
        free_only=free_only,
        available_only=available_only,
        event_type=parsed_type,
        status=parsed_status,
        sort=effective_sort,
        user_id=user_id if effective_sort == "for_you" else None,
        day_of_week=day_of_week,
        time_of_day=time_of_day,
        has_image=has_image,
        has_price=has_price,
        page=page,
        per_page=per_page,
    )


def serialize_event(event: Event) -> dict[str, Any]:
    """Serialize an Event instance to a JSON-safe dictionary.

    Args:
        event: The Event instance to serialize.

    Returns:
        Dictionary representation of the event.
    """
    return {
        "id": str(event.id),
        "venue_id": str(event.venue_id),
        "title": event.title,
        "slug": event.slug,
        "description": event.description,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "doors_at": event.doors_at.isoformat() if event.doors_at else None,
        "artists": event.artists or [],
        "genres": event.genres or [],
        "spotify_artist_ids": event.spotify_artist_ids or [],
        "image_url": event.image_url,
        "ticket_url": event.ticket_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "prices_refreshed_at": (
            event.prices_refreshed_at.isoformat() if event.prices_refreshed_at else None
        ),
        "source_url": event.source_url,
        "venue": _serialize_venue_with_city(event),
        "created_at": event.created_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
    }


def serialize_event_summary(event: Event) -> dict[str, Any]:
    """Serialize an Event to a compact summary for list views.

    Args:
        event: The Event instance to serialize.

    Returns:
        Compact dictionary representation of the event.
    """
    return {
        "id": str(event.id),
        "title": event.title,
        "slug": event.slug,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "artists": event.artists or [],
        "genres": event.genres or [],
        "image_url": event.image_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "prices_refreshed_at": (
            event.prices_refreshed_at.isoformat() if event.prices_refreshed_at else None
        ),
        "status": event.status.value,
        "venue": _serialize_venue_with_city(event),
    }


def _serialize_venue_with_city(event: Event) -> dict[str, Any] | None:
    """Serialize the event's venue with its city inline.

    Browse cards need venue name and the city/region the venue lives in.
    The relationship is configured with ``lazy="selectin"`` so this read
    does not trigger an extra round-trip per event.

    Args:
        event: The parent Event instance.

    Returns:
        Venue summary with nested city, or None if venue is unloaded.
    """
    if event.venue is None:
        return None
    city = event.venue.city
    return {
        "id": str(event.venue.id),
        "name": event.venue.name,
        "slug": event.venue.slug,
        "city": (
            {
                "id": str(city.id),
                "name": city.name,
                "slug": city.slug,
                "state": city.state,
                "region": city.region,
            }
            if city is not None
            else None
        ),
    }


def format_event_feed(events: list[Event], generated_at: datetime) -> str:
    """Format events as plain text for the AI-readable feed endpoint.

    Produces a human- and AI-readable text feed as specified in CLAUDE.md
    for the GET /api/v1/feed/events endpoint.

    Args:
        events: List of Event instances to include in the feed.
        generated_at: Timestamp for the feed header.

    Returns:
        Plain text string of the formatted event feed.
    """
    lines: list[str] = []
    lines.append(
        f"Washington DC Concerts — Updated {generated_at.strftime('%Y-%m-%d %H:%M ET')}"
    )
    lines.append("")

    today = generated_at.date()

    tonight_events = [e for e in events if e.starts_at and e.starts_at.date() == today]
    upcoming_events = [e for e in events if e.starts_at and e.starts_at.date() > today]

    if tonight_events:
        lines.append("TONIGHT")
        for event in tonight_events:
            lines.append(_format_feed_line(event))
        lines.append("")

    if upcoming_events:
        lines.append("UPCOMING")
        for event in upcoming_events:
            date_str = event.starts_at.strftime("%a %b %d")
            lines.append(_format_feed_line(event, date_prefix=date_str))
        lines.append("")

    return "\n".join(lines)


def list_tonight_map_events(
    session: Session,
    *,
    region: str = "DMV",
    now_utc: datetime | None = None,
    genres: list[str] | None = None,
) -> dict[str, Any]:
    """Return the envelope of today's pinnable events for the map surface.

    "Tonight" is defined as the current calendar day in Eastern time (the
    DMV region), so events scheduled late in the evening still count as
    tonight even when the UTC wallclock has rolled into the next day.
    Venues without coordinates are dropped because the map UI has no way
    to render them.

    Args:
        session: Active SQLAlchemy session.
        region: City region filter — defaults to ``"DMV"`` since this is
            the DC map. Exposed so the same function can be exercised
            from tests or other regions later.
        now_utc: Clock anchor in UTC. Injected in tests to pin the ET
            day window; defaults to :func:`datetime.now` in production.
        genres: Optional genre overlap filter for the filter bar.

    Returns:
        Standard envelope dict ``{"data": [...], "meta": {...}}`` where
        each row carries enough shape to render a map pin plus a
        preview card: id, slug, title, artists, genres, image_url,
        min_price, starts_at, and a venue block with latitude and
        longitude.
    """
    anchor = (now_utc or datetime.now(UTC)).astimezone(_ET_ZONE)
    today = anchor.date()

    events, _total = events_repo.list_events(
        session,
        region=region,
        date_from=today,
        date_to=today,
        genres=genres,
        status=EventStatus.CONFIRMED,
        page=1,
        per_page=100,
    )
    pins = [_serialize_tonight_event(event) for event in events if _has_coords(event)]
    return {
        "data": pins,
        "meta": {"count": len(pins), "date": today.isoformat()},
    }


def list_events_near(
    session: Session,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    window: NearMeWindow = "tonight",
    region: str = "DMV",
    limit: int = 50,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return upcoming events within ``radius_km`` of a lat/lng, nearest first.

    Powers the "Shows Near Me" surface. Unlike the Tonight map (which
    sweeps the whole DMV), this endpoint narrows to a radius around
    the user's current location and supports a small time window —
    ``"tonight"`` for today only, ``"week"`` for the next seven days.

    The distance filter is computed in Python via the haversine formula
    so the repo query doesn't need PostGIS. The DMV dataset is small
    enough (< 100 venues) that the post-fetch filter is cheap.

    Args:
        session: Active SQLAlchemy session.
        latitude: WGS-84 latitude of the user's current location.
        longitude: WGS-84 longitude of the user's current location.
        radius_km: Maximum great-circle distance to include, in km.
        window: ``"tonight"`` (today only, ET) or ``"week"`` (today
            through today + 6 days, ET).
        region: City region filter; defaults to ``"DMV"``.
        limit: Maximum rows returned after distance sort. Capped
            internally at 100 since the map surface renders one pin per.
        now_utc: Clock anchor in UTC, injected by tests to pin the ET
            day window. Defaults to :func:`datetime.now` in production.

    Returns:
        Standard envelope ``{"data": [...], "meta": {...}}``. Each row
        has the tonight-map pin shape plus a ``distance_km`` float.
        Meta echoes the caller's center, radius, window, and the
        resolved ``date_from`` / ``date_to`` bounds.

    Raises:
        ValidationError: If ``window`` is not one of the supported
            literals.
    """
    if window not in _NEAR_ME_WINDOWS:
        raise ValidationError(
            f"Invalid window: '{window}'. Valid values: {sorted(_NEAR_ME_WINDOWS)}"
        )
    capped_limit = max(1, min(limit, 100))

    anchor = (now_utc or datetime.now(UTC)).astimezone(_ET_ZONE)
    day_from = anchor.date()
    day_to = day_from if window == "tonight" else day_from + timedelta(days=6)

    events, _total = events_repo.list_events(
        session,
        region=region,
        date_from=day_from,
        date_to=day_to,
        status=EventStatus.CONFIRMED,
        page=1,
        per_page=200,
    )

    rows: list[dict[str, Any]] = []
    for event in events:
        if not _has_coords(event):
            continue
        venue = event.venue
        assert venue is not None  # narrowed by _has_coords
        distance = _haversine_km(
            latitude,
            longitude,
            venue.latitude,  # type: ignore[arg-type]
            venue.longitude,  # type: ignore[arg-type]
        )
        if distance > radius_km:
            continue
        payload = _serialize_tonight_event(event)
        payload["distance_km"] = round(distance, 3)
        rows.append(payload)

    rows.sort(key=lambda r: r["distance_km"])
    rows = rows[:capped_limit]

    return {
        "data": rows,
        "meta": {
            "count": len(rows),
            "center": {"latitude": latitude, "longitude": longitude},
            "radius_km": radius_km,
            "window": window,
            "date_from": day_from.isoformat(),
            "date_to": day_to.isoformat(),
        },
    }


def _haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance between two WGS-84 points in kilometres.

    Uses the standard haversine formula with the IUGG mean Earth radius
    (6371.0088 km) so distances are accurate to within ~0.3% for the
    sub-100-km ranges the near-me surface cares about.

    Args:
        lat1: Latitude of point A in decimal degrees.
        lon1: Longitude of point A in decimal degrees.
        lat2: Latitude of point B in decimal degrees.
        lon2: Longitude of point B in decimal degrees.

    Returns:
        Great-circle distance in kilometres.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c


def _has_coords(event: Event) -> bool:
    """Return True when the event's venue can be placed on a map.

    Args:
        event: Event instance with its venue relationship loaded.

    Returns:
        True if the venue relationship is present and both ``latitude``
        and ``longitude`` are non-null.
    """
    venue = event.venue
    return (
        venue is not None and venue.latitude is not None and venue.longitude is not None
    )


def _serialize_tonight_event(event: Event) -> dict[str, Any]:
    """Serialize an event into the compact shape the map surface consumes.

    Drops moderation-only fields (raw_data, source_url, external_id) and
    wraps the venue with just the fields the pin and preview card need
    (name, slug, and the two coordinates the map has to have).

    Args:
        event: An Event instance whose venue has coordinates.

    Returns:
        JSON-safe dict for the ``data`` array on ``/maps/tonight``.
    """
    venue = event.venue
    return {
        "id": str(event.id),
        "slug": event.slug,
        "title": event.title,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "artists": event.artists or [],
        "genres": event.genres or [],
        "image_url": event.image_url,
        "ticket_url": event.ticket_url,
        "min_price": event.min_price,
        "max_price": event.max_price,
        "venue": {
            "id": str(venue.id),
            "name": venue.name,
            "slug": venue.slug,
            "latitude": venue.latitude,
            "longitude": venue.longitude,
        },
    }


def _format_feed_line(
    event: Event,
    date_prefix: str | None = None,
) -> str:
    """Format a single event as a plain text feed line.

    Args:
        event: The Event instance.
        date_prefix: Optional date string to prepend.

    Returns:
        Formatted feed line string.
    """
    venue_name = event.venue.name if event.venue else "TBA"
    parts: list[str] = []

    artist_str = ", ".join(event.artists) if event.artists else event.title

    if date_prefix:
        parts.append(f"{date_prefix}: {artist_str} @ {venue_name}")
    else:
        parts.append(f"{artist_str} @ {venue_name}")

    if event.doors_at:
        parts.append(f"Doors {event.doors_at.strftime('%I:%M %p')}")

    if event.min_price is not None:
        parts.append(f"From ${event.min_price:.0f}")

    parts.append(event.status.value)

    return "• " + " — ".join(parts)
