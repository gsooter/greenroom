"""Tour-announcement notification dispatch.

When the scraper ingests a new event, every user with any signal for
the event's performers (followed artist, music-service top artist,
recently-played artist) becomes a candidate for a real-time push.

This module owns the orchestration:

1. Look up the freshly-created event and its performers.
2. Resolve performer names to :class:`Artist` rows so we can match
   on UUIDs (FollowedArtist) and on Spotify ids
   (User.spotify_top_artist_ids array overlap).
3. For each matched user, build a :class:`NotificationTrigger` and
   call the unified dispatcher.

Deduplication is handled by the dispatcher's ``notification_log``
write — a re-run of the scraper that re-discovers the same event
will re-enqueue the same triggers, but the unique constraint on
``(user, type, event_id, channel)`` keeps each user from receiving
the notification twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name
from backend.data.models.artists import Artist
from backend.data.models.events import Event
from backend.data.models.onboarding import FollowedArtist
from backend.data.models.users import User
from backend.services import notification_dispatcher
from backend.services.notification_dispatcher import (
    NotificationTrigger,
    NotificationType,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

logger = get_logger(__name__)


def dispatch_for_event(session: Session, event_id: uuid.UUID) -> dict[str, int]:
    """Fan out tour-announcement triggers for one freshly-added event.

    Args:
        session: Active SQLAlchemy session. The caller commits.
        event_id: UUID of the event whose performers should drive
            the notification fan-out.

    Returns:
        A small summary the Celery wrapper logs as a single line:
        ``{"performers": ..., "candidate_users": ..., "dispatched": ...}``.
    """
    event = session.get(Event, event_id)
    if event is None:
        return {"performers": 0, "candidate_users": 0, "dispatched": 0}

    artist_names = list(event.artists or [])
    if not artist_names:
        return {"performers": 0, "candidate_users": 0, "dispatched": 0}

    artists = _lookup_artists_by_name(session, artist_names)
    if not artists:
        return {"performers": len(artist_names), "candidate_users": 0, "dispatched": 0}

    user_ids = _find_users_with_signal(session, artists)
    if not user_ids:
        return {
            "performers": len(artist_names),
            "candidate_users": 0,
            "dispatched": 0,
        }

    headliner_name = artist_names[0]
    venue_name = event.venue.name if event.venue else ""
    url = _build_event_url(event)
    date_label = _format_date_label(event)

    dispatched = 0
    for user_id in user_ids:
        trigger = NotificationTrigger(
            user_id=user_id,
            notification_type=NotificationType.TOUR_ANNOUNCEMENT,
            dedupe_key=str(event_id),
            payload={
                "event_id": str(event_id),
                "performer_name": headliner_name,
                "venue_name": venue_name,
                "date_label": date_label,
                "url": url,
            },
            trigger_time=datetime.now(UTC),
        )
        result = notification_dispatcher.dispatch(session, trigger)
        if result.push == "sent" or result.queued_until is not None:
            dispatched += 1

    return {
        "performers": len(artist_names),
        "candidate_users": len(user_ids),
        "dispatched": dispatched,
    }


def _lookup_artists_by_name(session: Session, names: list[str]) -> list[Artist]:
    """Resolve performer name strings to Artist rows.

    Uses the same ``normalize_artist_name`` helper the scraper uses
    when upserting artist rows so the lookup matches the stored key.

    Args:
        session: Active SQLAlchemy session.
        names: Performer names from ``Event.artists``.

    Returns:
        List of :class:`Artist` rows that resolved. Names without a
        matching row (artists that were never enriched / never
        scraped before) are silently dropped — they cannot generate
        a follow signal anyway.
    """
    normalized = {normalize_artist_name(n) for n in names if n}
    if not normalized:
        return []
    rows = (
        session.execute(select(Artist).where(Artist.normalized_name.in_(normalized)))
        .scalars()
        .all()
    )
    return list(rows)


def _find_users_with_signal(session: Session, artists: list[Artist]) -> list[uuid.UUID]:
    """Find every user who has *any* signal for the given artists.

    Signals considered:

    * Explicit follow (``FollowedArtist``).
    * Spotify top-artist overlap (``users.spotify_top_artist_ids``).
    * Apple Music top-artist overlap (``users.apple_top_artist_ids``).
    * Tidal top-artist overlap (``users.tidal_top_artist_ids``).

    Args:
        session: Active SQLAlchemy session.
        artists: Resolved :class:`Artist` rows for the event's
            performers.

    Returns:
        Deduplicated list of user UUIDs. Order is unspecified; the
        dispatcher fan-out is per-user and has no batch dependency.
    """
    artist_ids = [a.id for a in artists]
    spotify_ids = [a.spotify_id for a in artists if a.spotify_id]

    candidates: set[uuid.UUID] = set()

    follow_stmt = select(FollowedArtist.user_id).where(
        FollowedArtist.artist_id.in_(artist_ids)
    )
    candidates.update(session.execute(follow_stmt).scalars().all())

    if spotify_ids:
        # ARRAY overlap (`&&`) returns a row when any element of the
        # left-hand array appears in the right-hand array. The GIN
        # index on ``users.spotify_top_artist_ids`` makes this cheap
        # even at full table size.
        signal_stmt = select(User.id).where(
            or_(
                User.spotify_top_artist_ids.op("&&")(spotify_ids),
                User.spotify_recent_artist_ids.op("&&")(spotify_ids),
                User.apple_top_artist_ids.op("&&")(spotify_ids),
                User.tidal_top_artist_ids.op("&&")(spotify_ids),
            )
        )
        candidates.update(session.execute(signal_stmt).scalars().all())

    return list(candidates)


def _build_event_url(event: Event) -> str:
    """Compose the public event-detail URL.

    Args:
        event: The event to link to.

    Returns:
        Absolute URL of the event's public detail page.
    """
    base = get_settings().frontend_base_url.rstrip("/")
    return f"{base}/events/{event.slug}"


def _format_date_label(event: Event) -> str:
    """Return a short human-readable date for the push body.

    Args:
        event: The event whose start time should be formatted.

    Returns:
        A ``"Sat, Jun 14"``-style label localized to the venue's
        timezone, or an empty string when the event has no start
        time (defensive — every scraped event should have one).
    """
    if event.starts_at is None:
        return ""
    starts = event.starts_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)
    tz_name = (
        event.venue.city.timezone
        if event.venue and event.venue.city
        else "America/New_York"
    )
    local = starts.astimezone(ZoneInfo(tz_name))
    return local.strftime("%a, %b %-d")
