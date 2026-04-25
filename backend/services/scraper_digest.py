"""Daily fleet-health digest for the scraper system.

Posts a single Slack/email summary once a day so the operator gets a
predictable proactive signal even when no alerts have fired. The digest
covers three things the per-event alerts cannot:

* Quiet successes — every per-venue alert is a problem signal, so a
  silent week could mean "all is well" or "nothing is running." The
  digest distinguishes the two.
* Slow-burn outages — a venue that has been failing under-cooldown
  for days still surfaces here on the next morning's digest.
* Fleet posture — a single line ("28/30 healthy") is the at-a-glance
  number the operator wants before opening Slack threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import scraper_alerts as alerts_repo
from backend.data.repositories import scraper_runs as runs_repo
from backend.scraper.config.venues import VenueScraperConfig, get_enabled_configs
from backend.scraper.notifier import send_alert

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.scraper import ScraperRun, ScraperRunStatus

logger = get_logger(__name__)

DIGEST_WINDOW_HOURS = 24
"""Lookback window the digest reports against."""

STALE_SUCCESS_HOURS = 36
"""Hours since last successful run that mark a venue as stale.

The nightly schedule runs every 24 hours, so 36 hours covers a missed
nightly with a buffer for retries — anything older points at a venue
that has not produced a healthy run in more than one cycle.
"""


@dataclass(frozen=True)
class VenueHealth:
    """Snapshot of a single venue's recent health.

    Attributes:
        slug: Venue slug used for display.
        display_name: Human-readable venue name from the scraper config.
        last_status: Status of the most recent run, or ``None`` when the
            venue has no runs in history at all.
        last_run_at: ``started_at`` of the most recent run, in UTC.
        last_success_at: ``started_at`` of the most recent SUCCESS run,
            in UTC. ``None`` when the venue has never succeeded.
        consecutive_failures: Number of FAILED runs at the head of the
            history. Zero when the most recent run is not a failure.
    """

    slug: str
    display_name: str
    last_status: ScraperRunStatus | None
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int


@dataclass(frozen=True)
class DigestPayload:
    """Rendered digest, ready to hand to :func:`send_alert`.

    Attributes:
        title: Slack/email title line.
        message: Multi-line message body.
        details: Structured fields for the Slack attachment + email tail.
        severity: Highest severity present in the digest. Drives the
            Slack color; ``"info"`` when the fleet is fully healthy,
            ``"warning"`` for stale venues, ``"error"`` for active
            failures.
    """

    title: str
    message: str
    details: dict[str, Any]
    severity: str
    snapshots: list[VenueHealth] = field(default_factory=list)


def build_digest_payload(
    session: Session,
    *,
    now: datetime | None = None,
) -> DigestPayload:
    """Build the daily digest from current DB state.

    Args:
        session: Active SQLAlchemy session.
        now: Override the current timestamp; primarily for tests.

    Returns:
        A fully rendered :class:`DigestPayload` ready to send.
    """
    current = now if now is not None else datetime.now(UTC)
    window_start = current - timedelta(hours=DIGEST_WINDOW_HOURS)
    stale_cutoff = current - timedelta(hours=STALE_SUCCESS_HOURS)

    configs = get_enabled_configs()
    snapshots = [_build_snapshot(session, config) for config in configs]

    failing = [s for s in snapshots if s.consecutive_failures > 0]
    stale = [
        s
        for s in snapshots
        if s.consecutive_failures == 0
        and (s.last_success_at is None or s.last_success_at < stale_cutoff)
    ]
    healthy_count = len(snapshots) - len(failing) - len(stale)
    alert_count = alerts_repo.count_active_alerts(session, since=window_start)

    severity = _pick_severity(failing=failing, stale=stale)
    title = _render_title(
        total=len(snapshots),
        healthy=healthy_count,
        failing=len(failing),
        stale=len(stale),
    )
    message = _render_message(
        current=current,
        snapshots=snapshots,
        failing=failing,
        stale=stale,
        healthy_count=healthy_count,
        alert_count=alert_count,
    )
    details: dict[str, Any] = {
        "total": len(snapshots),
        "healthy": healthy_count,
        "failing": len(failing),
        "stale": len(stale),
        "alerts_24h": alert_count,
    }
    return DigestPayload(
        title=title,
        message=message,
        details=details,
        severity=severity,
        snapshots=snapshots,
    )


def send_daily_digest_for_session(
    session: Session,
    *,
    now: datetime | None = None,
) -> DigestPayload:
    """Build and dispatch the daily digest using a caller-supplied session.

    The notifier is invoked with ``alert_key=None`` so the digest never
    suppresses itself — running it on schedule is the whole point.

    Args:
        session: Active SQLAlchemy session shared with the dedup table.
        now: Override the current timestamp; primarily for tests.

    Returns:
        The :class:`DigestPayload` that was dispatched.
    """
    payload = build_digest_payload(session, now=now)
    send_alert(
        title=payload.title,
        message=payload.message,
        severity=payload.severity,
        details=payload.details,
        alert_key=None,
        session=session,
    )
    return payload


@shared_task(name="backend.services.scraper_digest.send_daily_digest")  # type: ignore[untyped-decorator]
def send_daily_digest() -> dict[str, Any]:
    """Celery task: send the daily fleet-health digest.

    Owns its own short-lived session so the dispatcher commits cleanly
    even when the digest runs while no other work is in flight. Errors
    bubble up so Celery records the task as failed and retries logic
    in the scheduler can react.

    Returns:
        The digest details payload, useful for Flower inspection.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            payload = send_daily_digest_for_session(session)
            session.commit()
            return {
                "title": payload.title,
                "severity": payload.severity,
                **payload.details,
            }
        except Exception:
            session.rollback()
            raise


def _build_snapshot(
    session: Session,
    config: VenueScraperConfig,
) -> VenueHealth:
    """Assemble a single venue's :class:`VenueHealth` snapshot.

    Args:
        session: Active SQLAlchemy session.
        config: Venue config from :mod:`backend.scraper.config.venues`.

    Returns:
        The rendered :class:`VenueHealth` for that venue.
    """
    recent = runs_repo.get_recent_runs(session, config.venue_slug, limit=1)
    last_run: ScraperRun | None = recent[0] if recent else None
    last_success = runs_repo.get_last_successful_run(session, config.venue_slug)
    consecutive = runs_repo.count_consecutive_failed_runs(session, config.venue_slug)

    return VenueHealth(
        slug=config.venue_slug,
        display_name=config.display_name or config.venue_slug,
        last_status=last_run.status if last_run else None,
        last_run_at=_to_utc(last_run.started_at) if last_run else None,
        last_success_at=(_to_utc(last_success.started_at) if last_success else None),
        consecutive_failures=consecutive,
    )


def _pick_severity(
    *,
    failing: list[VenueHealth],
    stale: list[VenueHealth],
) -> str:
    """Pick the digest severity based on fleet posture.

    Args:
        failing: Venues whose head run is FAILED.
        stale: Venues with no recent successful run.

    Returns:
        ``"error"``, ``"warning"``, or ``"info"`` — used by the Slack
        attachment color and by humans skimming subject lines.
    """
    if failing:
        return "error"
    if stale:
        return "warning"
    return "info"


def _render_title(
    *,
    total: int,
    healthy: int,
    failing: int,
    stale: int,
) -> str:
    """Render the digest title line.

    Args:
        total: Total enabled venues.
        healthy: Venues with a fresh successful run.
        failing: Venues whose most recent run failed.
        stale: Venues with no recent successful run.

    Returns:
        Single-line title summarizing the fleet.
    """
    base = f"Daily scraper digest — {healthy}/{total} healthy"
    if failing or stale:
        parts: list[str] = []
        if failing:
            parts.append(f"{failing} failing")
        if stale:
            parts.append(f"{stale} stale")
        return f"{base} ({', '.join(parts)})"
    return base


def _render_message(
    *,
    current: datetime,
    snapshots: list[VenueHealth],
    failing: list[VenueHealth],
    stale: list[VenueHealth],
    healthy_count: int,
    alert_count: int,
) -> str:
    """Render the multi-line digest body.

    Args:
        current: Timestamp the digest was assembled at.
        snapshots: Per-venue snapshots for the entire enabled fleet.
        failing: Subset whose most recent run failed.
        stale: Subset with no recent successful run.
        healthy_count: Number of venues considered healthy.
        alert_count: Number of distinct alerts that fired in the window.

    Returns:
        A multi-line plaintext body suitable for Slack and email.
    """
    lines: list[str] = []
    lines.append(
        f"Window: last {DIGEST_WINDOW_HOURS}h ending {current:%Y-%m-%d %H:%M UTC}."
    )
    lines.append(
        f"Fleet: {len(snapshots)} venues — "
        f"{healthy_count} healthy, {len(failing)} failing, {len(stale)} stale."
    )
    lines.append(f"Alerts fired in window: {alert_count}.")

    if failing:
        lines.append("")
        lines.append("Failing venues (most recent run is FAILED):")
        for snap in sorted(failing, key=lambda s: -s.consecutive_failures):
            lines.append(
                f"  • {snap.display_name} ({snap.slug}) — "
                f"{snap.consecutive_failures} consecutive failure"
                f"{'s' if snap.consecutive_failures != 1 else ''}, "
                f"last success {_format_relative(snap.last_success_at, current)}"
            )

    if stale:
        lines.append("")
        lines.append(f"Stale venues (no successful run in {STALE_SUCCESS_HOURS}h):")
        epoch = datetime.min.replace(tzinfo=UTC)
        for snap in sorted(stale, key=lambda s: s.last_success_at or epoch):
            lines.append(
                f"  • {snap.display_name} ({snap.slug}) — "
                f"last success {_format_relative(snap.last_success_at, current)}"
            )

    if not failing and not stale:
        lines.append("")
        lines.append("All enabled scrapers ran cleanly in the last cycle. ✅")

    return "\n".join(lines)


def _format_relative(value: datetime | None, current: datetime) -> str:
    """Render a UTC timestamp as a short "Nh/d ago" string for the digest.

    Args:
        value: The timestamp to format. ``None`` means "never recorded".
        current: The reference "now" timestamp.

    Returns:
        Phrase like ``"3h ago"`` or ``"never"``.
    """
    if value is None:
        return "never"
    delta = current - value
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _to_utc(value: datetime) -> datetime:
    """Return ``value`` as a tz-aware UTC datetime.

    SQLAlchemy can hand back naive timestamps depending on dialect and
    column flavor. The digest does arithmetic against ``datetime.now(UTC)``
    so we normalize defensively.

    Args:
        value: A possibly-naive datetime.

    Returns:
        The same instant expressed in UTC with tzinfo attached.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
