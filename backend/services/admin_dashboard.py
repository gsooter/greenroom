"""Admin dashboard summary — system counts, recent activity, health, leaderboard.

The dashboard is a single read-only aggregation that the admin landing
page renders without any per-section roundtripping. Each section is
backed by a small helper here so callers can render selectively (the
CLI ``hydration-stats`` command, for example, only needs the
hydration leaderboard).

The leaderboard sections — "most hydrated last 30 days" and "best
hydration candidates" — are the operational view that drives ongoing
catalog growth (Decision 068). They are intentionally cheap queries
designed to run every time the dashboard loads; if the artist catalog
ever grows past low six figures we can revisit caching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, select

from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.events import Event, EventStatus
from backend.data.models.hydration_log import HydrationLog
from backend.data.models.notification_log import NotificationLog
from backend.data.models.notifications import EmailDigestLog
from backend.data.models.push import PushSubscription
from backend.data.models.scraper import ScraperRun, ScraperRunStatus
from backend.data.models.users import (
    DigestFrequency,
    MusicServiceConnection,
    OAuthProvider,
    User,
)
from backend.data.models.venues import Venue
from backend.services.artist_hydration import (
    DAILY_HYDRATION_CAP,
    MIN_SIMILARITY_SCORE,
    get_daily_hydration_count,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Health thresholds — tuned in code so the dashboard renders consistent
# colors across environments. Adjusting any of these is a one-PR change.
# ---------------------------------------------------------------------------

PUSH_DELIVERY_GREEN: float = 0.95
"""≥ this fraction of dispatched pushes confirmed delivered → green."""

PUSH_DELIVERY_YELLOW: float = 0.80
"""≥ this fraction → yellow; below → red."""

EMAIL_BOUNCE_GREEN: float = 0.02
"""≤ this bounce rate → green."""

EMAIL_BOUNCE_YELLOW: float = 0.05
"""≤ this bounce rate → yellow; above → red."""

ACTIVE_USER_WINDOW = timedelta(days=30)
"""Window the "active user" count uses against ``last_login_at``."""


@dataclass(frozen=True)
class CountBreakdown:
    """A counted total with a labeled breakdown dictionary.

    Attributes:
        total: Top-line count.
        breakdown: Sub-categories that sum to (or annotate) the total.
    """

    total: int
    breakdown: dict[str, int]


@dataclass(frozen=True)
class ActivityWindow:
    """Per-window activity counters for the dashboard.

    Attributes:
        label: Human-readable window label (``"24 hours"``).
        new_users: New user rows created in the window.
        new_events: Events scraped in the window.
        push_sends: Successful push notifications dispatched in the
            window.
        email_sends: Email notifications dispatched in the window.
        hydrations_run: Number of hydration calls in the window.
        hydration_artists_added: Total artists added by those calls.
    """

    label: str
    new_users: int
    new_events: int
    push_sends: int
    email_sends: int
    hydrations_run: int
    hydration_artists_added: int


@dataclass(frozen=True)
class HealthSignal:
    """One row in the health-signals section.

    Attributes:
        label: Human-readable identifier rendered next to the value.
        value: Pre-formatted display string (``"99.1%"``, ``"4 hrs ago"``).
        status: ``"green"`` / ``"yellow"`` / ``"red"`` for color coding.
        detail: Optional secondary detail for tooltip/expand.
    """

    label: str
    value: str
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class LeaderboardArtist:
    """Row in the "most hydrated" leaderboard.

    Attributes:
        artist_id: UUID of the artist row.
        artist_name: Display name.
        hydration_count: Number of similar-artists added during the
            window via hydration of this artist.
    """

    artist_id: uuid.UUID
    artist_name: str
    hydration_count: int


@dataclass(frozen=True)
class HydrationCandidateArtist:
    """Row in the "best hydration candidates" leaderboard.

    Attributes:
        artist_id: UUID of the seed artist.
        artist_name: Display name.
        candidate_count: Count of similar-artists above the threshold
            that don't yet exist in the database.
        top_candidate_name: Display name of the top similar-artist
            candidate, for preview.
    """

    artist_id: uuid.UUID
    artist_name: str
    candidate_count: int
    top_candidate_name: str | None


@dataclass(frozen=True)
class DashboardSnapshot:
    """Complete snapshot rendered on the admin dashboard landing page.

    Attributes:
        users: Total + breakdown for users.
        artists: Total + breakdown for artists (original vs hydrated).
        events: Total + breakdown for events (upcoming/past/cancelled).
        venues: Total + breakdown for venues (active/inactive).
        music_connections: Per-provider connected-service counts.
        push_subscriptions: Active vs disabled push counters.
        email_enabled_users: Count of users whose digest is on.
        activity: Three :class:`ActivityWindow` rows (24h / 7d / 30d).
        health: List of :class:`HealthSignal` rows.
        most_hydrated: Leaderboard for last 30 days.
        best_candidates: "Best hydration candidates" leaderboard.
        daily_hydration_remaining: Slots left under
            :data:`DAILY_HYDRATION_CAP`.
    """

    users: CountBreakdown
    artists: CountBreakdown
    events: CountBreakdown
    venues: CountBreakdown
    music_connections: dict[str, int]
    push_subscriptions: dict[str, int]
    email_enabled_users: int
    activity: list[ActivityWindow] = field(default_factory=list)
    health: list[HealthSignal] = field(default_factory=list)
    most_hydrated: list[LeaderboardArtist] = field(default_factory=list)
    best_candidates: list[HydrationCandidateArtist] = field(default_factory=list)
    daily_hydration_remaining: int = 0


def build_dashboard_snapshot(session: Session) -> DashboardSnapshot:
    """Assemble every section of the dashboard in one pass.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        A populated :class:`DashboardSnapshot`.
    """
    cap_remaining = max(0, DAILY_HYDRATION_CAP - get_daily_hydration_count(session))
    return DashboardSnapshot(
        users=_count_users(session),
        artists=_count_artists(session),
        events=_count_events(session),
        venues=_count_venues(session),
        music_connections=_count_music_connections(session),
        push_subscriptions=_count_push_subscriptions(session),
        email_enabled_users=_count_email_enabled_users(session),
        activity=[
            _build_activity_window(session, label="24 hours", hours=24),
            _build_activity_window(session, label="7 days", hours=24 * 7),
            _build_activity_window(session, label="30 days", hours=24 * 30),
        ],
        health=_build_health_signals(session),
        most_hydrated=most_hydrated_leaderboard(session),
        best_candidates=best_hydration_candidates(session),
        daily_hydration_remaining=cap_remaining,
    )


def _count_users(session: Session) -> CountBreakdown:
    """Total users plus active/inactive/deactivated breakdown.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        :class:`CountBreakdown` with the full split.
    """
    cutoff = datetime.now(UTC) - ACTIVE_USER_WINDOW
    total = session.execute(select(func.count(User.id))).scalar_one() or 0
    active = (
        session.execute(
            select(func.count(User.id))
            .where(User.is_active.is_(True))
            .where(User.last_login_at >= cutoff)
        ).scalar_one()
        or 0
    )
    signed_in = (
        session.execute(
            select(func.count(User.id)).where(User.is_active.is_(True))
        ).scalar_one()
        or 0
    )
    deactivated = (
        session.execute(
            select(func.count(User.id)).where(User.is_active.is_(False))
        ).scalar_one()
        or 0
    )
    return CountBreakdown(
        total=int(total),
        breakdown={
            "active_last_30d": int(active),
            "signed_in_inactive": int(signed_in - active),
            "deactivated": int(deactivated),
        },
    )


def _count_artists(session: Session) -> CountBreakdown:
    """Total artists plus original/hydrated breakdown.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        :class:`CountBreakdown` distinguishing scraper-seeded artists
        from those added via hydration (Decision 067).
    """
    total = session.execute(select(func.count(Artist.id))).scalar_one() or 0
    hydrated = (
        session.execute(
            select(func.count(Artist.id)).where(Artist.hydration_source.is_not(None))
        ).scalar_one()
        or 0
    )
    return CountBreakdown(
        total=int(total),
        breakdown={
            "original": int(total - hydrated),
            "hydrated": int(hydrated),
        },
    )


def _count_events(session: Session) -> CountBreakdown:
    """Total events plus upcoming/past/cancelled breakdown."""
    now = datetime.now(UTC)
    total = session.execute(select(func.count(Event.id))).scalar_one() or 0
    upcoming = (
        session.execute(
            select(func.count(Event.id))
            .where(Event.starts_at >= now)
            .where(Event.status != EventStatus.CANCELLED)
        ).scalar_one()
        or 0
    )
    past = (
        session.execute(
            select(func.count(Event.id)).where(Event.starts_at < now)
        ).scalar_one()
        or 0
    )
    cancelled = (
        session.execute(
            select(func.count(Event.id)).where(Event.status == EventStatus.CANCELLED)
        ).scalar_one()
        or 0
    )
    return CountBreakdown(
        total=int(total),
        breakdown={
            "upcoming": int(upcoming),
            "past": int(past),
            "cancelled": int(cancelled),
        },
    )


def _count_venues(session: Session) -> CountBreakdown:
    """Total venues plus active/inactive breakdown."""
    total = session.execute(select(func.count(Venue.id))).scalar_one() or 0
    active = (
        session.execute(
            select(func.count(Venue.id)).where(Venue.is_active.is_(True))
        ).scalar_one()
        or 0
    )
    return CountBreakdown(
        total=int(total),
        breakdown={
            "active": int(active),
            "inactive": int(total - active),
        },
    )


def _count_music_connections(session: Session) -> dict[str, int]:
    """Per-provider music-service connection counts.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Mapping of provider name to count. Providers without any rows
        appear with a value of ``0`` so the dashboard has a stable
        shape across environments.
    """
    rows = session.execute(
        select(
            MusicServiceConnection.provider,
            func.count(MusicServiceConnection.id),
        ).group_by(MusicServiceConnection.provider)
    ).all()
    counts: dict[str, int] = {p.value: 0 for p in OAuthProvider}
    for provider, count in rows:
        counts[provider.value] = int(count or 0)
    return counts


def _count_push_subscriptions(session: Session) -> dict[str, int]:
    """Active vs disabled push subscriptions."""
    total = session.execute(select(func.count(PushSubscription.id))).scalar_one() or 0
    disabled = (
        session.execute(
            select(func.count(PushSubscription.id)).where(
                PushSubscription.disabled_at.is_not(None)
            )
        ).scalar_one()
        or 0
    )
    return {
        "active": int(total - disabled),
        "disabled": int(disabled),
    }


def _count_email_enabled_users(session: Session) -> int:
    """Users whose digest frequency is anything other than ``never``."""
    return int(
        session.execute(
            select(func.count(User.id)).where(
                User.digest_frequency != DigestFrequency.NEVER
            )
        ).scalar_one()
        or 0
    )


def _build_activity_window(
    session: Session, *, label: str, hours: int
) -> ActivityWindow:
    """Build one :class:`ActivityWindow` row.

    Args:
        session: Active SQLAlchemy session.
        label: Display label for the window.
        hours: How far back from now the window extends.

    Returns:
        Populated :class:`ActivityWindow`.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    new_users = (
        session.execute(
            select(func.count(User.id)).where(User.created_at >= cutoff)
        ).scalar_one()
        or 0
    )
    new_events = (
        session.execute(
            select(func.count(Event.id)).where(Event.created_at >= cutoff)
        ).scalar_one()
        or 0
    )
    push_sends = (
        session.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= cutoff)
            .where(NotificationLog.channel == "push")
        ).scalar_one()
        or 0
    )
    email_sends = (
        session.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= cutoff)
            .where(NotificationLog.channel == "email")
        ).scalar_one()
        or 0
    )
    hydration_rows = list(
        session.execute(
            select(
                func.count(HydrationLog.id),
                func.coalesce(
                    func.sum(func.cardinality(HydrationLog.added_artist_ids)), 0
                ),
            ).where(HydrationLog.created_at >= cutoff)
        ).all()
    )
    hydrations_run = int(hydration_rows[0][0]) if hydration_rows else 0
    hydration_added = int(hydration_rows[0][1]) if hydration_rows else 0
    return ActivityWindow(
        label=label,
        new_users=int(new_users),
        new_events=int(new_events),
        push_sends=int(push_sends),
        email_sends=int(email_sends),
        hydrations_run=hydrations_run,
        hydration_artists_added=hydration_added,
    )


def _build_health_signals(session: Session) -> list[HealthSignal]:
    """Compute the four health signals in display order.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Ordered list of :class:`HealthSignal` rows.
    """
    return [
        _scraper_health(session),
        _push_delivery_health(session),
        _email_bounce_health(session),
        _recommendation_cache_health(session),
    ]


def _scraper_health(session: Session) -> HealthSignal:
    """Scraper fleet health — last successful run and stale-venue count.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Health signal row. Status is red if no successful scrape ran
        in the last 24h, yellow if there are stale venues but at least
        one recent success, green otherwise.
    """
    now = datetime.now(UTC)
    last_success_at = session.execute(
        select(func.max(ScraperRun.started_at)).where(
            ScraperRun.status == ScraperRunStatus.SUCCESS
        )
    ).scalar_one()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    last_24h_count = (
        session.execute(
            select(func.count(func.distinct(ScraperRun.venue_slug)))
            .where(ScraperRun.status == ScraperRunStatus.SUCCESS)
            .where(ScraperRun.started_at >= last_24h)
        ).scalar_one()
        or 0
    )
    stale_count = (
        session.execute(
            select(func.count(func.distinct(ScraperRun.venue_slug))).where(
                ScraperRun.started_at < last_7d
            )
        ).scalar_one()
        or 0
    )
    if last_success_at is None or last_success_at < last_24h:
        status = "red"
    elif stale_count > 0:
        status = "yellow"
    else:
        status = "green"
    value = "never" if last_success_at is None else last_success_at.isoformat()
    detail = (
        f"{last_24h_count} venues scraped in last 24h; "
        f"{stale_count} venues stale > 7 days"
    )
    return HealthSignal(
        label="Last successful scrape", value=value, status=status, detail=detail
    )


def _push_delivery_health(session: Session) -> HealthSignal:
    """Push delivery rate health from the notification log.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Push delivery health signal. ``NotificationLog`` rows are
        only written on successful dispatch, so the rate is computed
        as ``successful / (successful + disabled-in-last-24h)`` —
        a coarse but useful proxy until the dispatcher emits an
        explicit failure log.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    success = (
        session.execute(
            select(func.count(NotificationLog.id))
            .where(NotificationLog.sent_at >= cutoff)
            .where(NotificationLog.channel == "push")
        ).scalar_one()
        or 0
    )
    failed = (
        session.execute(
            select(func.count(PushSubscription.id)).where(
                PushSubscription.last_failure_at >= cutoff
            )
        ).scalar_one()
        or 0
    )
    total = success + failed
    if total == 0:
        return HealthSignal(
            label="Push delivery (24h)", value="no traffic", status="green"
        )
    rate = success / total
    pct = f"{rate * 100:.1f}%"
    if rate >= PUSH_DELIVERY_GREEN:
        status = "green"
    elif rate >= PUSH_DELIVERY_YELLOW:
        status = "yellow"
    else:
        status = "red"
    return HealthSignal(
        label="Push delivery (24h)",
        value=pct,
        status=status,
        detail=f"{success} ok / {failed} failed",
    )


def _email_bounce_health(session: Session) -> HealthSignal:
    """Email bounce rate over the last 7 days from the digest log."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    sent = (
        session.execute(
            select(func.count(EmailDigestLog.id)).where(
                EmailDigestLog.sent_at >= cutoff
            )
        ).scalar_one()
        or 0
    )
    bounced = (
        session.execute(
            select(func.count(EmailDigestLog.id))
            .where(EmailDigestLog.sent_at >= cutoff)
            .where(
                case(
                    (
                        EmailDigestLog.metadata_json["bounced"].as_boolean(),
                        1,
                    ),
                    else_=0,
                )
                == 1
            )
        ).scalar_one()
        or 0
    )
    if sent == 0:
        return HealthSignal(
            label="Email bounce rate (7d)", value="no traffic", status="green"
        )
    rate = bounced / sent
    pct = f"{rate * 100:.1f}%"
    if rate <= EMAIL_BOUNCE_GREEN:
        status = "green"
    elif rate <= EMAIL_BOUNCE_YELLOW:
        status = "yellow"
    else:
        status = "red"
    return HealthSignal(
        label="Email bounce rate (7d)",
        value=pct,
        status=status,
        detail=f"{bounced} bounced / {sent} sent",
    )


def _recommendation_cache_health(_session: Session) -> HealthSignal:
    """Placeholder for the recommendation engine cache hit rate.

    The cache lives in Redis today and is not introspected from the
    Postgres session — until we wire a dedicated counter, the
    dashboard reports ``unknown`` rather than a misleading number.

    Args:
        _session: Active SQLAlchemy session (unused).

    Returns:
        Placeholder health signal.
    """
    return HealthSignal(
        label="Recommendation cache hit rate",
        value="unknown",
        status="yellow",
        detail="Wire Redis-backed counters to populate this signal.",
    )


def most_hydrated_leaderboard(
    session: Session, *, days: int = 30, limit: int = 10
) -> list[LeaderboardArtist]:
    """Source artists with the most hydrations in the last ``days`` days.

    Args:
        session: Active SQLAlchemy session.
        days: Window length.
        limit: Maximum rows to return.

    Returns:
        Ordered list of :class:`LeaderboardArtist`.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            HydrationLog.source_artist_id,
            Artist.name,
            func.coalesce(
                func.sum(func.cardinality(HydrationLog.added_artist_ids)), 0
            ).label("count"),
        )
        .join(Artist, Artist.id == HydrationLog.source_artist_id)
        .where(HydrationLog.created_at >= cutoff)
        .group_by(HydrationLog.source_artist_id, Artist.name)
        .order_by(func.sum(func.cardinality(HydrationLog.added_artist_ids)).desc())
        .limit(limit)
    )
    return [
        LeaderboardArtist(
            artist_id=row.source_artist_id,
            artist_name=row.name,
            hydration_count=int(row.count),
        )
        for row in session.execute(stmt).all()
    ]


def best_hydration_candidates(
    session: Session, *, limit: int = 10
) -> list[HydrationCandidateArtist]:
    """Artists with the most eligible-but-not-yet-added similar artists.

    "Eligible" here means similarity ≥ :data:`MIN_SIMILARITY_SCORE` and
    the similar artist does not currently exist in the database
    (``similar_artist_id IS NULL`` after the resolution pass).

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum rows to return.

    Returns:
        Ordered list of :class:`HydrationCandidateArtist`.
    """
    threshold = MIN_SIMILARITY_SCORE
    counts_stmt = (
        select(
            ArtistSimilarity.source_artist_id,
            func.count(ArtistSimilarity.id).label("candidate_count"),
        )
        .where(ArtistSimilarity.similar_artist_id.is_(None))
        .where(ArtistSimilarity.similarity_score >= threshold)
        .group_by(ArtistSimilarity.source_artist_id)
        .order_by(func.count(ArtistSimilarity.id).desc())
        .limit(limit)
    )
    rows = list(session.execute(counts_stmt).all())
    if not rows:
        return []

    source_ids = [row.source_artist_id for row in rows]
    name_rows = session.execute(
        select(Artist.id, Artist.name).where(Artist.id.in_(source_ids))
    ).all()
    name_by_id = {artist_id: name for artist_id, name in name_rows}

    out: list[HydrationCandidateArtist] = []
    for row in rows:
        top_stmt = (
            select(ArtistSimilarity.similar_artist_name)
            .where(ArtistSimilarity.source_artist_id == row.source_artist_id)
            .where(ArtistSimilarity.similar_artist_id.is_(None))
            .where(ArtistSimilarity.similarity_score >= threshold)
            .order_by(ArtistSimilarity.similarity_score.desc())
            .limit(1)
        )
        top = session.execute(top_stmt).scalar_one_or_none()
        out.append(
            HydrationCandidateArtist(
                artist_id=row.source_artist_id,
                artist_name=name_by_id.get(row.source_artist_id, ""),
                candidate_count=int(row.candidate_count),
                top_candidate_name=top,
            )
        )
    return out


def serialize_dashboard_snapshot(snapshot: DashboardSnapshot) -> dict[str, Any]:
    """Render :class:`DashboardSnapshot` for the admin REST response.

    Args:
        snapshot: The snapshot to serialize.

    Returns:
        Plain JSONB-safe dictionary.
    """
    return {
        "users": _breakdown_to_dict(snapshot.users),
        "artists": _breakdown_to_dict(snapshot.artists),
        "events": _breakdown_to_dict(snapshot.events),
        "venues": _breakdown_to_dict(snapshot.venues),
        "music_connections": snapshot.music_connections,
        "push_subscriptions": snapshot.push_subscriptions,
        "email_enabled_users": snapshot.email_enabled_users,
        "activity": [
            {
                "label": w.label,
                "new_users": w.new_users,
                "new_events": w.new_events,
                "push_sends": w.push_sends,
                "email_sends": w.email_sends,
                "hydrations_run": w.hydrations_run,
                "hydration_artists_added": w.hydration_artists_added,
            }
            for w in snapshot.activity
        ],
        "health": [
            {
                "label": h.label,
                "value": h.value,
                "status": h.status,
                "detail": h.detail,
            }
            for h in snapshot.health
        ],
        "most_hydrated": [
            {
                "artist_id": str(row.artist_id),
                "artist_name": row.artist_name,
                "hydration_count": row.hydration_count,
            }
            for row in snapshot.most_hydrated
        ],
        "best_candidates": [
            {
                "artist_id": str(row.artist_id),
                "artist_name": row.artist_name,
                "candidate_count": row.candidate_count,
                "top_candidate_name": row.top_candidate_name,
            }
            for row in snapshot.best_candidates
        ],
        "daily_hydration_remaining": snapshot.daily_hydration_remaining,
    }


def _breakdown_to_dict(breakdown: CountBreakdown) -> dict[str, Any]:
    """Serialize one :class:`CountBreakdown` row.

    Args:
        breakdown: Source row.

    Returns:
        Plain dict.
    """
    return {"total": breakdown.total, "breakdown": breakdown.breakdown}
