"""Repository functions for event and ticket pricing database access.

All database queries related to events and ticket pricing snapshots
are defined here. No other module should query these tables directly.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from backend.data.models.events import (
    Event,
    EventPricingLink,
    EventStatus,
    EventType,
    TicketPricingSnapshot,
)

# ---------------------------------------------------------------------------
# Event queries
# ---------------------------------------------------------------------------


def get_event_by_id(session: Session, event_id: uuid.UUID) -> Event | None:
    """Fetch an event by its primary key.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event to fetch.

    Returns:
        The Event if found, otherwise None.
    """
    return session.get(Event, event_id)


def get_event_by_slug(session: Session, slug: str) -> Event | None:
    """Fetch an event by its URL slug.

    Args:
        session: Active SQLAlchemy session.
        slug: URL-safe slug identifier.

    Returns:
        The Event if found, otherwise None.
    """
    stmt = select(Event).where(Event.slug == slug)
    return session.execute(stmt).scalar_one_or_none()


def get_event_by_external_id(
    session: Session,
    external_id: str,
    source_platform: str,
) -> Event | None:
    """Fetch an event by its external platform ID.

    Used by scrapers to check if an event already exists before inserting.

    Args:
        session: Active SQLAlchemy session.
        external_id: The event's ID on the source platform.
        source_platform: Name of the source platform.

    Returns:
        The Event if found, otherwise None.
    """
    stmt = select(Event).where(
        Event.external_id == external_id,
        Event.source_platform == source_platform,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_all_event_artist_names(session: Session) -> list[str]:
    """Return every distinct artist name that appears on any event row.

    Used by the one-time artist enrichment backfill (see
    :mod:`backend.scripts.backfill_artist_enrichment`) to seed the
    ``artists`` table from events that were scraped before the ingestion
    path learned to upsert artists. Reads only the ``artists`` column so
    we never pull a full Event row into memory for this scan.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        A list of raw artist name strings in no particular order,
        preserving the original casing from whichever event first
        surfaced each name. The caller is responsible for collapsing
        duplicates via :func:`backend.core.text.normalize_artist_name`.
    """
    stmt = select(Event.artists).where(Event.artists.is_not(None))
    seen: set[str] = set()
    names: list[str] = []
    for (raw_list,) in session.execute(stmt).all():
        if not raw_list:
            continue
        for name in raw_list:
            if not isinstance(name, str):
                continue
            stripped = name.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            names.append(stripped)
    return names


_UNAVAILABLE_STATUSES: tuple[EventStatus, ...] = (
    EventStatus.CANCELLED,
    EventStatus.SOLD_OUT,
    EventStatus.PAST,
)

# Time-of-day buckets are defined in venue-local time (America/New_York).
# Boundaries are half-open at the start and exclusive at the end so a show
# at exactly 18:00 ET counts as evening, not early.
_TIME_OF_DAY_BUCKETS: dict[str, tuple[int, int]] = {
    "early": (0, 18),
    "evening": (18, 22),
    "late": (22, 24),
}


def list_events(
    session: Session,
    *,
    city_id: uuid.UUID | None = None,
    region: str | None = None,
    venue_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    genres: list[str] | None = None,
    spotify_artist_ids: list[str] | None = None,
    artist_search: str | None = None,
    price_max: float | None = None,
    free_only: bool = False,
    available_only: bool = False,
    event_type: EventType | None = None,
    status: EventStatus | None = None,
    sort: str | None = None,
    user_id: uuid.UUID | None = None,
    day_of_week: list[int] | None = None,
    time_of_day: list[str] | None = None,
    has_image: bool = False,
    has_price: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """Fetch events with optional filters and pagination.

    Args:
        session: Active SQLAlchemy session.
        city_id: Filter to events in venues belonging to this city.
        region: Filter to events in venues in cities with this region
            (e.g., "DMV"). Combined with city_id via AND.
        venue_ids: Filter to specific venues. None means all venues.
        date_from: Start of date range (inclusive). None means no lower bound.
        date_to: End of date range (inclusive). None means no upper bound.
        genres: Filter to events matching any of these genres (overlap).
        spotify_artist_ids: Filter to events whose ``spotify_artist_ids``
            array overlaps any of these IDs. An empty list short-circuits
            to zero results — the caller asked for "events matching
            artists I follow" but supplied no artists.
        artist_search: Case-insensitive substring on the ``artists``
            JSONB array. ``"phoebe"`` matches an event with
            ``artists = ["Phoebe Bridgers"]``. Whitespace-only strings
            are ignored.
        price_max: Upper bound (inclusive) for ``min_price``. Events
            with ``min_price IS NULL`` are excluded so unpriced shows
            don't pollute "under $X" results.
        free_only: Restrict to events whose ``min_price`` is exactly
            zero. Mutually exclusive in spirit with ``price_max``; the
            caller decides which to apply.
        available_only: Drop events whose ``status`` is cancelled,
            sold out, or past. Useful as a default for browse listings.
        event_type: Filter to a specific event type.
        status: Filter to a specific event status. Takes precedence over
            ``available_only`` when both are passed.
        sort: Sort key. ``"for_you"`` orders by the matching
            recommendation score descending (NULL last) and tiebreaks on
            ``starts_at`` ascending — requires ``user_id``. Anything else
            (including ``None`` or ``"date"``) falls back to chronological
            ``starts_at`` ascending.
        user_id: Greenroom user UUID used to source recommendation rows
            for the ``for_you`` sort. Ignored otherwise.
        day_of_week: Filter to events whose ET-local weekday matches any
            of these values. Postgres day-of-week convention: 0=Sunday
            through 6=Saturday. ``None`` skips the filter.
        time_of_day: Filter to events whose ET-local start hour falls in
            one of the named buckets — ``"early"`` (before 18:00),
            ``"evening"`` (18:00-22:00), ``"late"`` (22:00 and later).
            Multiple buckets are OR'd. Unknown bucket names are ignored.
        has_image: Restrict to events with a non-null ``image_url`` so
            grid-style listings render cleanly.
        has_price: Restrict to events whose ``min_price`` is set, so
            "From $X" cards never render with no price.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Maximum 100. Defaults to 20.

    Returns:
        Tuple of (events list, total count for pagination).
    """
    from backend.data.models.cities import City
    from backend.data.models.recommendations import Recommendation
    from backend.data.models.venues import Venue

    per_page = min(per_page, 100)

    base = select(Event)
    needs_venue_join = city_id is not None or region is not None
    if needs_venue_join:
        base = base.join(Venue, Event.venue_id == Venue.id)

    if city_id is not None:
        base = base.where(Venue.city_id == city_id)

    if region is not None:
        base = base.join(City, Venue.city_id == City.id).where(City.region == region)

    if venue_ids is not None:
        base = base.where(Event.venue_id.in_(venue_ids))

    if date_from is not None:
        base = base.where(
            Event.starts_at >= datetime.combine(date_from, datetime.min.time())
        )

    if date_to is not None:
        base = base.where(
            Event.starts_at <= datetime.combine(date_to, datetime.max.time())
        )

    if genres is not None:
        base = base.where(Event.genres.overlap(genres))

    if spotify_artist_ids is not None:
        if not spotify_artist_ids:
            # Empty list = "match nothing" rather than "no filter".
            base = base.where(false())
        else:
            base = base.where(Event.spotify_artist_ids.overlap(spotify_artist_ids))

    if artist_search is not None and artist_search.strip():
        pattern = f"%{artist_search.strip()}%"
        # `array_to_string` flattens the artists list so a single ILIKE
        # check covers every element. Avoids the unnest+lateral subquery
        # pattern at the cost of pulling each row's artist list into a
        # text scan — fine for our row counts.
        base = base.where(func.array_to_string(Event.artists, "|").ilike(pattern))

    if free_only:
        base = base.where(Event.min_price == 0)
    elif price_max is not None:
        base = base.where(Event.min_price.is_not(None)).where(
            Event.min_price <= price_max
        )

    if has_image:
        base = base.where(Event.image_url.is_not(None))

    if has_price:
        base = base.where(Event.min_price.is_not(None))

    if day_of_week:
        # Convert UTC `starts_at` to ET before extracting the weekday so
        # a Friday-night show that crosses into Saturday UTC still counts
        # as Friday for the user. Postgres `dow` returns 0..6 with 0=Sun.
        et_starts = func.timezone("America/New_York", Event.starts_at)
        base = base.where(func.extract("dow", et_starts).in_(day_of_week))

    if time_of_day:
        ranges: list[tuple[int, int]] = []
        for bucket in time_of_day:
            window = _TIME_OF_DAY_BUCKETS.get(bucket)
            if window is not None:
                ranges.append(window)
        if ranges:
            et_starts = func.timezone("America/New_York", Event.starts_at)
            hour = func.extract("hour", et_starts)
            clauses = [hour.between(start, end - 1) for start, end in ranges]
            base = base.where(or_(*clauses))
        else:
            # Caller asked for time_of_day but every bucket was unknown;
            # treat that as "match nothing" so the bug surfaces in the UI
            # rather than silently returning the unfiltered list.
            base = base.where(false())

    if event_type is not None:
        base = base.where(Event.event_type == event_type)

    if status is not None:
        base = base.where(Event.status == status)
    elif available_only:
        base = base.where(Event.status.notin_(_UNAVAILABLE_STATUSES))

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    use_for_you = sort == "for_you" and user_id is not None
    if use_for_you:
        # LEFT JOIN keeps unscored events in the listing — they sort
        # behind every recommended one because of NULLS LAST, which
        # matches the digest's ranking semantics.
        stmt = (
            base.outerjoin(
                Recommendation,
                (Recommendation.event_id == Event.id)
                & (Recommendation.user_id == user_id),
            )
            .order_by(
                Recommendation.score.desc().nulls_last(),
                Event.starts_at.asc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    else:
        stmt = (
            base.order_by(Event.starts_at).offset((page - 1) * per_page).limit(per_page)
        )

    events = list(session.execute(stmt).scalars().all())
    return events, total


def list_events_by_venue(
    session: Session,
    venue_id: uuid.UUID,
    *,
    upcoming_only: bool = True,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Event], int]:
    """Fetch events for a specific venue with pagination.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue.
        upcoming_only: If True, only return future events. Defaults to True.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 20.

    Returns:
        Tuple of (events list, total count for pagination).
    """
    base = select(Event).where(Event.venue_id == venue_id)

    if upcoming_only:
        base = base.where(Event.starts_at >= func.now())

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = base.order_by(Event.starts_at).offset((page - 1) * per_page).limit(per_page)
    events = list(session.execute(stmt).scalars().all())
    return events, total


def list_events_by_artist_ids(
    session: Session,
    spotify_artist_ids: list[str],
    *,
    upcoming_only: bool = True,
) -> list[Event]:
    """Fetch events matching any of the given Spotify artist IDs.

    Uses the GIN index on spotify_artist_ids for fast overlap queries.
    This is the core query powering the recommendation engine.

    Args:
        session: Active SQLAlchemy session.
        spotify_artist_ids: Spotify artist IDs to match against.
        upcoming_only: If True, only return future events. Defaults to True.

    Returns:
        List of matching Event instances ordered by start date.
    """
    stmt = select(Event).where(Event.spotify_artist_ids.overlap(spotify_artist_ids))

    if upcoming_only:
        stmt = stmt.where(Event.starts_at >= func.now())

    stmt = stmt.order_by(Event.starts_at)
    return list(session.execute(stmt).scalars().all())


def create_event(session: Session, **kwargs: Any) -> Event:
    """Create a new event.

    Args:
        session: Active SQLAlchemy session.
        **kwargs: Event attribute names and values. Must include at minimum
            venue_id, title, slug, and starts_at.

    Returns:
        The newly created Event instance.
    """
    event = Event(**kwargs)
    session.add(event)
    session.flush()
    return event


def update_event(
    session: Session,
    event: Event,
    **kwargs: Any,
) -> Event:
    """Update an event's attributes.

    Args:
        session: Active SQLAlchemy session.
        event: The Event instance to update.
        **kwargs: Attribute names and their new values.

    Returns:
        The updated Event instance.
    """
    for key, value in kwargs.items():
        if hasattr(event, key):
            setattr(event, key, value)
    session.flush()
    return event


def count_events_by_venue(
    session: Session,
    venue_id: uuid.UUID,
    *,
    upcoming_only: bool = True,
) -> int:
    """Count events for a venue.

    Args:
        session: Active SQLAlchemy session.
        venue_id: UUID of the venue.
        upcoming_only: If True, only count future events. Defaults to True.

    Returns:
        The number of matching events.
    """
    stmt = select(func.count()).where(Event.venue_id == venue_id)
    if upcoming_only:
        stmt = stmt.where(Event.starts_at >= func.now())
    return session.execute(stmt).scalar_one()


# ---------------------------------------------------------------------------
# Ticket pricing snapshot queries
# ---------------------------------------------------------------------------


def create_ticket_snapshot(
    session: Session,
    *,
    event_id: uuid.UUID,
    source: str,
    min_price: float | None = None,
    max_price: float | None = None,
    average_price: float | None = None,
    listing_count: int | None = None,
    currency: str = "USD",
    raw_data: dict[str, Any] | None = None,
) -> TicketPricingSnapshot:
    """Create a new ticket pricing snapshot.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event this pricing is for.
        source: Platform name (e.g., "seatgeek", "stubhub").
        min_price: Minimum ticket price.
        max_price: Maximum ticket price.
        average_price: Average ticket price.
        listing_count: Number of active listings.
        currency: Currency code. Defaults to USD.
        raw_data: Full pricing payload from the source.

    Returns:
        The newly created TicketPricingSnapshot instance.
    """
    snapshot = TicketPricingSnapshot(
        event_id=event_id,
        source=source,
        min_price=min_price,
        max_price=max_price,
        average_price=average_price,
        listing_count=listing_count,
        currency=currency,
        raw_data=raw_data,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def list_ticket_snapshots(
    session: Session,
    event_id: uuid.UUID,
    *,
    source: str | None = None,
    limit: int = 50,
) -> list[TicketPricingSnapshot]:
    """Fetch ticket pricing snapshots for an event.

    Returns snapshots in reverse chronological order for price
    history and trend display.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        source: Optional platform filter.
        limit: Maximum number of snapshots to return. Defaults to 50.

    Returns:
        List of TicketPricingSnapshot instances, newest first.
    """
    stmt = select(TicketPricingSnapshot).where(
        TicketPricingSnapshot.event_id == event_id
    )

    if source is not None:
        stmt = stmt.where(TicketPricingSnapshot.source == source)

    stmt = stmt.order_by(TicketPricingSnapshot.created_at.desc()).limit(limit)

    return list(session.execute(stmt).scalars().all())


def get_latest_ticket_snapshot(
    session: Session,
    event_id: uuid.UUID,
    source: str,
) -> TicketPricingSnapshot | None:
    """Fetch the most recent ticket pricing snapshot for an event and source.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        source: Platform name.

    Returns:
        The latest TicketPricingSnapshot if any exist, otherwise None.
    """
    stmt = (
        select(TicketPricingSnapshot)
        .where(
            TicketPricingSnapshot.event_id == event_id,
            TicketPricingSnapshot.source == source,
        )
        .order_by(TicketPricingSnapshot.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def list_latest_snapshots_by_source(
    session: Session,
    event_id: uuid.UUID,
) -> list[TicketPricingSnapshot]:
    """Return the most recent snapshot per ``source`` for one event.

    The For-You and event-detail UIs render one row per provider — the
    cheapest one anchors the buy CTA and the rest fill out a price-
    comparison list. A naive ``DISTINCT ON (source)`` works here because
    snapshots are append-only and ``(event_id, created_at desc)`` is
    indexed.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.

    Returns:
        One snapshot per source the event has been priced under,
        newest-first within the (event_id, source) tuple.
    """
    stmt = (
        select(TicketPricingSnapshot)
        .where(TicketPricingSnapshot.event_id == event_id)
        .order_by(
            TicketPricingSnapshot.source.asc(),
            TicketPricingSnapshot.created_at.desc(),
        )
        .distinct(TicketPricingSnapshot.source)
    )
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Pricing-link queries
# ---------------------------------------------------------------------------


def upsert_pricing_link(
    session: Session,
    *,
    event_id: uuid.UUID,
    source: str,
    url: str,
    affiliate_url: str | None = None,
    is_active: bool = True,
    currency: str = "USD",
    seen_at: datetime | None = None,
) -> EventPricingLink:
    """Create or update the buy-URL row for one (event, source).

    Decoupled from snapshots so a provider that finds zero listings on
    this sweep still keeps its URL on file — the next sweep that finds
    inventory just bumps ``last_active_at`` instead of re-deriving the
    URL. ``last_seen_at`` always advances; ``last_active_at`` only
    advances when ``is_active`` is true.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        source: Provider identifier (e.g., ``"seatgeek"``).
        url: Canonical buy URL.
        affiliate_url: Affiliate-tagged URL when the provider has one;
            preferred over ``url`` in the UI.
        is_active: Whether this refresh found live listings at the URL.
        currency: Currency code the source quotes prices in.
        seen_at: Timestamp to record. Defaults to ``datetime.now(UTC)``.

    Returns:
        The created or updated :class:`EventPricingLink` row.
    """
    now = seen_at or datetime.now(UTC)
    stmt = select(EventPricingLink).where(
        EventPricingLink.event_id == event_id,
        EventPricingLink.source == source,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is None:
        link = EventPricingLink(
            event_id=event_id,
            source=source,
            url=url,
            affiliate_url=affiliate_url,
            currency=currency,
            is_active=is_active,
            last_seen_at=now,
            last_active_at=now if is_active else None,
        )
        session.add(link)
        session.flush()
        return link

    existing.url = url
    existing.affiliate_url = affiliate_url
    existing.currency = currency
    existing.is_active = is_active
    existing.last_seen_at = now
    if is_active:
        existing.last_active_at = now
    session.flush()
    return existing


def list_pricing_links(
    session: Session,
    event_id: uuid.UUID,
    *,
    only_active: bool = False,
) -> list[EventPricingLink]:
    """List the per-source buy-URL rows for an event.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        only_active: When True, drop rows whose latest refresh found
            zero listings. The detail UI passes ``True`` so we only
            render Buy CTAs for surfaces that currently have inventory.

    Returns:
        Pricing-link rows ordered by source name for stable rendering.
    """
    stmt = select(EventPricingLink).where(EventPricingLink.event_id == event_id)
    if only_active:
        stmt = stmt.where(EventPricingLink.is_active.is_(True))
    stmt = stmt.order_by(EventPricingLink.source.asc())
    return list(session.execute(stmt).scalars().all())


def list_events_for_pricing_sweep(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> list[Event]:
    """Return upcoming events ordered by stalest-first for the daily sweep.

    The Celery sweep iterates this list, hitting every Tier A and Tier B
    provider per event. Stalest first so a sweep that gets interrupted
    still spreads coverage rather than re-refreshing the same head of
    the list. Past events are excluded — once a show has happened, no
    new pricing is meaningful.

    Args:
        session: Active SQLAlchemy session.
        now: Reference clock; injected by tests so the upcoming-only
            filter is deterministic. Defaults to ``datetime.now(UTC)``.
        limit: Maximum events to return per sweep run. The 5am cron
            takes one batch; the next morning's run picks up where this
            one left off if the catalog ever exceeds the limit.

    Returns:
        Events with ``starts_at >= now``, ordered by
        ``prices_refreshed_at`` ascending (NULLs first so brand-new
        events get priced before re-pricing recently-swept ones).
    """
    anchor = now or datetime.now(UTC)
    stmt = (
        select(Event)
        .where(Event.starts_at >= anchor)
        .order_by(
            Event.prices_refreshed_at.asc().nulls_first(),
            Event.starts_at.asc(),
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def get_latest_pricing_refresh(
    session: Session,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the most recent ``prices_refreshed_at`` across upcoming events.

    Powers the listing-page freshness banner ("Pricing updated X ago").
    Past events are excluded so a stale stamp on a finished show
    doesn't anchor the banner forever.

    Args:
        session: Active SQLAlchemy session.
        now: Reference clock; injected by tests so the upcoming-only
            filter is deterministic. Defaults to ``datetime.now(UTC)``.

    Returns:
        The largest non-null ``prices_refreshed_at`` timestamp on any
        upcoming event, or ``None`` if no upcoming event has been
        priced yet.
    """
    anchor = now or datetime.now(UTC)
    stmt = select(func.max(Event.prices_refreshed_at)).where(Event.starts_at >= anchor)
    return session.execute(stmt).scalar_one_or_none()


def stamp_prices_refreshed_at(
    session: Session,
    event_id: uuid.UUID,
    *,
    refreshed_at: datetime | None = None,
) -> datetime:
    """Mark an event as just-refreshed for the cooldown gate.

    The service layer calls this after a refresh sweep completes so the
    next request inside the cooldown window short-circuits to the
    persisted snapshots.

    Args:
        session: Active SQLAlchemy session.
        event_id: UUID of the event.
        refreshed_at: Timestamp to write. Defaults to ``datetime.now(UTC)``.

    Returns:
        The timestamp that was written, so callers can echo it back to
        the API caller without re-reading the row.
    """
    stamp = refreshed_at or datetime.now(UTC)
    event = session.get(Event, event_id)
    if event is None:
        return stamp
    event.prices_refreshed_at = stamp
    session.flush()
    return stamp
