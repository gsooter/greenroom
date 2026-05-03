"""Home page business logic.

Backs the signed-in home page experience that leads with personalized
content (Decision 063). Three responsibilities live here:

* Building the anchor-artist set that drives the "New since your last
  visit" query — followed artists + per-service top artists + Spotify
  recently-played.
* Querying upcoming events created since the user's last home visit
  whose performers overlap that anchor set, scoped to the user's
  preferred region when one is set.
* Updating ``users.last_home_visit_at`` after each home page render so
  the next visit's window starts where this one ended. The write is
  delegated to a Celery task (see :mod:`backend.services.home_tasks`)
  so the request thread never blocks on it.

The recommendation engine builds a richer anchor map for similarity
scoring; this module deliberately re-derives a simpler (display-name)
view because the new-since query only needs the names that actually
appear on event rows. Mirroring the engine's UUID-resolution path
would couple the two without buying anything for this surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus
from backend.data.models.onboarding import FollowedArtist, FollowedVenue
from backend.data.models.venues import Venue
from backend.data.repositories import regions as regions_repo
from backend.data.repositories import users as users_repo

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.users import User

# Fallback look-back for users who have never recorded a home page
# visit. Long enough that newly-signed-up users see something on the
# section's first appearance, but capped so accounts that pre-date the
# column don't pull in months of stale "new" announcements.
_FIRST_VISIT_LOOKBACK_DAYS = 30

# Cap on how many events the section returns. The frontend renders up
# to four inline and a "See all" link for the rest, so anything beyond
# 10 wastes payload.
_MAX_RESULTS = 10


def get_new_since_last_visit(
    session: Session,
    user: User,
    *,
    now: datetime | None = None,
    limit: int = _MAX_RESULTS,
) -> list[Event]:
    """Return upcoming events newly relevant to the user since their last visit.

    Filters:

    * ``Event.created_at`` strictly greater than the user's
      ``last_home_visit_at``. When that is null (first home page load
      after the column was introduced), falls back to a fixed
      ``_FIRST_VISIT_LOOKBACK_DAYS`` window so the section is populated
      rather than empty.
    * At least one entry in ``Event.artists`` matches the user's
      anchor-artist set (followed artists + per-service top artists +
      Spotify recently-played). When the anchor set is empty the
      function short-circuits to an empty list — without anchors the
      section has no meaningful definition of "newly relevant".
    * ``Event.starts_at >= now`` so past announcements aren't surfaced
      as new.
    * ``Event.status != cancelled`` for the same reason.
    * Venue belongs to a city in the user's preferred region when
      ``user.city_id`` resolves to one. Users without a preferred city
      get the full DMV by default — the home page never shows
      cross-region recommendations even when the user has friends in
      another market.

    Sorted by ``created_at DESC`` so the newest announcements lead, with
    ``starts_at ASC`` as a tiebreaker for batches scraped together.

    Args:
        session: Active SQLAlchemy session.
        user: The signed-in user.
        now: Override for the "current time" used for the upcoming
            filter. Tests pin this to a fixed instant; production
            leaves it ``None`` and falls back to ``datetime.now(UTC)``.
        limit: Maximum number of rows returned. Defaults to
            :data:`_MAX_RESULTS`.

    Returns:
        Up to ``limit`` :class:`Event` rows matching the filters above,
        newest-first. Empty list when the user has no anchors or when
        nothing new matches.
    """
    anchor_names = _collect_anchor_display_names(user)
    if not anchor_names:
        return []

    current_time = now or datetime.now(UTC)
    window_start = _resolve_window_start(user, current_time)

    stmt = (
        select(Event)
        .where(Event.created_at > window_start)
        .where(Event.starts_at >= current_time)
        .where(Event.status != EventStatus.CANCELLED)
        .where(Event.artists.op("&&")(list(anchor_names)))
    )

    region_id = _resolve_user_region_id(session, user)
    if region_id is not None:
        stmt = stmt.join(Venue, Event.venue_id == Venue.id).join(
            City, Venue.city_id == City.id
        )
        stmt = stmt.where(City.region_id == region_id)

    stmt = stmt.order_by(Event.created_at.desc(), Event.starts_at.asc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def has_signal(session: Session, user: User) -> bool:
    """Return True when the user has enough taste signal to personalize.

    The home page reframes around personalized content for users with
    "at least 3 follows OR a connected music service". This probe keeps
    that gate centralized so the route handler, the rec-engine empty-
    state path, and any future UI variant share one definition.

    Args:
        session: Active SQLAlchemy session, used to count follows.
        user: The signed-in user.

    Returns:
        True if the user has any cached music-service top-artists
        snapshot or has followed at least three artists/venues
        (combined).
    """
    if (
        user.spotify_top_artists
        or user.spotify_recent_artists
        or user.tidal_top_artists
        or user.apple_top_artists
    ):
        return True

    return _count_follows(session, user.id) >= 3


def update_last_home_visit_at(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    """Set ``users.last_home_visit_at`` to ``now``.

    The home page route fires this through a Celery task so the request
    thread never blocks on the write. Tests can call it directly to
    skip the fan-out and assert the side-effect.

    Args:
        session: Active SQLAlchemy session.
        user_id: Knuckles UUID of the user being updated.
        now: Override for the timestamp. Defaults to
            ``datetime.now(UTC)``.
    """
    user = users_repo.get_user_by_id(session, user_id)
    if user is None:
        return
    user.last_home_visit_at = now or datetime.now(UTC)
    session.flush()


def _resolve_window_start(user: User, now: datetime) -> datetime:
    """Return the inclusive ``created_at`` lower bound for the new-since query.

    Uses ``user.last_home_visit_at`` when set; otherwise falls back to
    a fixed ``_FIRST_VISIT_LOOKBACK_DAYS`` window so a fresh account or
    a pre-column user still sees a populated section on first paint.

    Args:
        user: The signed-in user.
        now: The current time used to compute the fallback window.

    Returns:
        A timezone-aware datetime that the caller compares against
        ``Event.created_at`` with strict-greater-than semantics.
    """
    last_visit = user.last_home_visit_at
    if last_visit is not None:
        return last_visit
    return now - timedelta(days=_FIRST_VISIT_LOOKBACK_DAYS)


def _resolve_user_region_id(session: Session, user: User) -> uuid.UUID | None:
    """Return the user's preferred-region UUID, or ``None`` when unset.

    Mirrors the resolution path the recommendation engine's
    actionability overlay uses (Decision 062) — a user without a
    preferred city is treated as "any DMV city is fine" so the new-
    since query falls back to no region filter rather than empty
    results.

    Args:
        session: Active SQLAlchemy session.
        user: The signed-in user.

    Returns:
        The region UUID the user's preferred city belongs to, or
        ``None`` when the user has no preferred city or the city has
        no region recorded.
    """
    if user.city_id is None:
        return None
    region = regions_repo.get_region_for_city(session, user.city_id)
    return region.id if region is not None else None


def _collect_anchor_display_names(user: User) -> set[str]:
    """Build the display-name set used for the new-since artist filter.

    Combines:

    * Per-service top-artists snapshots
      (``spotify_top_artists`` / ``tidal_top_artists`` / ``apple_top_artists``).
    * Spotify recently-played artist snapshots.

    Followed artists ride along inside the snapshots when the user
    explicitly followed someone they don't listen to, but the dedicated
    follows lookup is intentionally omitted here — the new-since query
    runs on every home page load, and a query-per-load JOIN against
    ``followed_artists`` would gain very little when the recommendation
    engine already factors follows into its top-of-page section.

    Args:
        user: The signed-in user.

    Returns:
        Set of artist display names matching the casing they were
        cached under. Used as the right-hand side of an
        ``Event.artists && <names>`` overlap query, so casing must
        match the scraper-stored names — fortunately the cached
        snapshots and the scraper output both come from the same
        upstream catalogs.
    """
    names: set[str] = set()
    sources: list[list[dict[str, object]] | None] = [
        user.spotify_top_artists,
        user.spotify_recent_artists,
        user.tidal_top_artists,
        user.apple_top_artists,
    ]
    for source in sources:
        for entry in source or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return names


def _count_follows(session: Session, user_id: uuid.UUID) -> int:
    """Return how many artists + venues the given user follows.

    Counted as a single query each per follow table; the home page
    only calls this when no music-service snapshot is present, so the
    extra round-trips are bounded to the no-signal path.

    Args:
        session: Active SQLAlchemy session.
        user_id: Knuckles UUID of the user.

    Returns:
        Combined count of followed artists and followed venues.
    """
    artist_count = session.execute(
        select(func.count())
        .select_from(FollowedArtist)
        .where(FollowedArtist.user_id == user_id)
    ).scalar_one()
    venue_count = session.execute(
        select(func.count())
        .select_from(FollowedVenue)
        .where(FollowedVenue.user_id == user_id)
    ).scalar_one()
    return int(artist_count) + int(venue_count)
