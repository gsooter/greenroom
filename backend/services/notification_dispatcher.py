"""Unified notification dispatcher.

A single :func:`dispatch` entry point routes a trigger to push, email,
or both based on:

* The notification *type* (push and email serve different purposes —
  see the channel routing table below).
* The recipient's :class:`NotificationPreferences` (per-channel
  toggles, quiet hours, frequency caps, paused-all).
* The recipient's email-bounce flag (skip email if bounced).
* The recipient's push subscriptions (skip push when none).

Channel philosophy (Decision 065): push is the high-signal,
"drop what you're doing" channel and is rate-limited at five per
day. Email is the exploratory, weekly-cadence channel and carries
the discovery-oriented surfaces (digest, staff picks, similar-artist
suggestions). The same trigger can fire on both channels — a tour
announcement might arrive as a push immediately and also appear in
that week's digest as a "newly announced" recap.

A trigger that lands during the user's quiet hours is *queued*
(via Celery's ``eta=``) for the next non-quiet hour, not dropped.
Tour-announcement pushes are intentionally time-sensitive but a
3 AM ping is worse than an 8 AM ping; queueing is the compromise.

The dispatcher itself is synchronous and DB-only. The transport
mechanics (Resend HTTP + Web Push HTTP) live in the modules it
calls. Keeping side effects local makes unit testing the routing
rules trivial — pass a fake send fn and assert the recorded log
rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from backend.core.logging import get_logger
from backend.data.repositories import notification_log as log_repo
from backend.data.repositories import notification_preferences as prefs_repo
from backend.data.repositories import push_subscriptions as push_repo
from backend.data.repositories import users as users_repo
from backend.services import push as push_service

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.notifications import NotificationPreferences
    from backend.data.models.users import User

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class NotificationType(StrEnum):
    """Stable string identifiers for every dispatchable trigger.

    Stored verbatim in ``notification_log.notification_type``.
    Adding a new type means adding a new entry here, plus a routing
    rule in :data:`_CHANNEL_ROUTING`, plus a renderer below. The
    dispatcher never has to know about the type's content shape.
    """

    TOUR_ANNOUNCEMENT = "tour_announcement"
    VENUE_ANNOUNCEMENT = "venue_announcement"
    SHOW_REMINDER_24H = "show_reminder_24h"
    SELLING_FAST = "selling_fast"
    WEEKLY_DIGEST = "weekly_digest"


class Channel(StrEnum):
    """Delivery channels the dispatcher supports."""

    PUSH = "push"
    EMAIL = "email"


@dataclass(frozen=True)
class NotificationTrigger:
    """A single dispatchable notification event.

    Attributes:
        user_id: UUID of the recipient.
        notification_type: Kind of notification (drives routing,
            rendering, and per-type preference lookup).
        dedupe_key: Trigger-anchored key. For event-type notifications
            this is the event UUID (so the same show can't push twice);
            for the weekly digest it is an ISO week string.
        payload: Free-form context for the renderer. Each renderer
            documents the keys it expects.
        trigger_time: When the trigger was raised. Defaults to "now"
            but can be overridden so a backfilled event uses the
            backfill date for quiet-hour math.
    """

    user_id: uuid.UUID
    notification_type: NotificationType
    dedupe_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    trigger_time: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one :func:`dispatch` call.

    Attributes:
        push: ``"sent"``, ``"skipped"`` (and a reason string), or
            ``None`` (channel not applicable to this trigger type).
        email: Same shape as ``push``.
        queued_until: When the trigger was deferred for quiet hours.
            ``None`` when no queueing happened.
    """

    push: str | None
    email: str | None
    queued_until: datetime | None = None


# ---------------------------------------------------------------------------
# Channel routing rules
# ---------------------------------------------------------------------------


# Channel routing table. The first column lists notification types and
# the second/third columns list which channels the dispatcher attempts
# for that type. Each (type, channel) pair maps to:
#   - the NotificationPreferences boolean column to consult, OR
#   - None when the channel is unconditionally enabled for this type
#     (e.g. weekly digest already gates on its own column inside the
#     existing legacy code path; the dispatcher only routes to it).
@dataclass(frozen=True)
class _ChannelRule:
    """How one (type, channel) pair routes through preferences.

    Attributes:
        prefs_field: NotificationPreferences boolean column name to
            consult, or None when the channel is always-on for this
            type.
        push_rate_limited: When True, the per-day push cap applies.
            Set to False for the test endpoint, which deliberately
            bypasses rate limiting.
    """

    prefs_field: str | None
    push_rate_limited: bool = True


_CHANNEL_ROUTING: dict[NotificationType, dict[Channel, _ChannelRule]] = {
    NotificationType.TOUR_ANNOUNCEMENT: {
        Channel.PUSH: _ChannelRule(prefs_field="artist_announcements"),
    },
    NotificationType.VENUE_ANNOUNCEMENT: {
        Channel.PUSH: _ChannelRule(prefs_field="venue_announcements"),
    },
    NotificationType.SHOW_REMINDER_24H: {
        Channel.PUSH: _ChannelRule(prefs_field="show_reminders"),
    },
    NotificationType.SELLING_FAST: {
        Channel.PUSH: _ChannelRule(prefs_field="selling_fast_alerts"),
    },
    NotificationType.WEEKLY_DIGEST: {
        # Weekly digest goes through the legacy
        # ``notifications.send_weekly_digest_to_user`` pipeline so the
        # dispatcher just records the log row; the actual send is
        # owned elsewhere. ``prefs_field=None`` because the legacy
        # path consults ``weekly_digest`` itself.
        Channel.EMAIL: _ChannelRule(prefs_field=None),
    },
}


# Five push notifications per 24 hours per user. Rolls over rather
# than caps cumulatively — the rate limit is a comfort floor, not a
# subscription cap.
_PUSH_PER_DAY_LIMIT: int = 5


# ---------------------------------------------------------------------------
# Dispatcher entry point
# ---------------------------------------------------------------------------


def dispatch(
    session: Session,
    trigger: NotificationTrigger,
    *,
    push_sender: Any | None = None,
) -> DispatchResult:
    """Route a single trigger to the appropriate channel(s).

    Args:
        session: Active SQLAlchemy session. The dispatcher writes
            log rows and updates push-subscription failure state in
            the same transaction; the caller commits.
        trigger: The notification to dispatch.
        push_sender: Override for the push send function (testing).
            Defaults to :func:`backend.services.push.send_to_user`.
            Must accept ``(session, user_id, payload)`` and return a
            :class:`backend.services.push.SendResult`.

    Returns:
        A :class:`DispatchResult` describing each channel's outcome.

    Raises:
        ValueError: If the trigger's ``notification_type`` is not
            registered in :data:`_CHANNEL_ROUTING`. This is a
            developer error — adding a new type means adding a
            routing rule.
    """
    routing = _CHANNEL_ROUTING.get(trigger.notification_type)
    if routing is None:
        raise ValueError(
            f"No routing rule for notification type {trigger.notification_type!r}"
        )

    user = users_repo.get_user_by_id(session, trigger.user_id)
    if user is None:
        return DispatchResult(push=None, email=None)

    prefs = prefs_repo.get_or_create_for_user(session, trigger.user_id)
    if prefs.paused_at is not None:
        return DispatchResult(
            push=("skipped:paused" if Channel.PUSH in routing else None),
            email=("skipped:paused" if Channel.EMAIL in routing else None),
        )

    queued_until: datetime | None = None
    if _is_in_quiet_hours(prefs, trigger.trigger_time):
        queued_until = _next_non_quiet_hour(prefs, trigger.trigger_time)
        # Mutate the trigger time on a fresh trigger so the queued
        # job re-enters dispatch with the post-quiet timestamp. The
        # actual deferral is a Celery responsibility — the caller
        # handles enqueuing with eta=queued_until.
        return DispatchResult(
            push=("queued" if Channel.PUSH in routing else None),
            email=("queued" if Channel.EMAIL in routing else None),
            queued_until=queued_until,
        )

    push_outcome: str | None = None
    email_outcome: str | None = None

    if Channel.PUSH in routing:
        push_outcome = _dispatch_push(
            session=session,
            user=user,
            prefs=prefs,
            trigger=trigger,
            rule=routing[Channel.PUSH],
            push_sender=push_sender or push_service.send_to_user,
        )

    if Channel.EMAIL in routing:
        email_outcome = _dispatch_email(
            session=session,
            user=user,
            prefs=prefs,
            trigger=trigger,
            rule=routing[Channel.EMAIL],
        )

    return DispatchResult(push=push_outcome, email=email_outcome)


# ---------------------------------------------------------------------------
# Channel-specific helpers
# ---------------------------------------------------------------------------


def _dispatch_push(
    *,
    session: Session,
    user: User,
    prefs: NotificationPreferences,
    trigger: NotificationTrigger,
    rule: _ChannelRule,
    push_sender: Any,
) -> str:
    """Apply the push routing rules and send if appropriate.

    Returns:
        Outcome string the caller folds into :class:`DispatchResult`.
    """
    if rule.prefs_field is not None and not getattr(prefs, rule.prefs_field):
        return f"skipped:prefs:{rule.prefs_field}"

    subs = push_repo.list_active_for_user(session, user.id)
    if not subs:
        return "skipped:no_subscriptions"

    if rule.push_rate_limited:
        recent = log_repo.count_recent_pushes(
            session, user.id, window_hours=24, now=trigger.trigger_time
        )
        if recent >= _PUSH_PER_DAY_LIMIT:
            return "skipped:rate_limited"

    payload = _render_push(trigger)
    if payload is None:
        return "skipped:no_renderer"

    claimed = log_repo.claim(
        session,
        user_id=user.id,
        notification_type=trigger.notification_type.value,
        dedupe_key=trigger.dedupe_key,
        channel=Channel.PUSH.value,
        payload=trigger.payload,
        now=trigger.trigger_time,
    )
    if not claimed:
        return "skipped:duplicate"

    result = push_sender(session, user.id, payload)
    if getattr(result, "succeeded", 0) > 0:
        return "sent"
    return "send_failed"


def _dispatch_email(
    *,
    session: Session,
    user: User,
    prefs: NotificationPreferences,
    trigger: NotificationTrigger,
    rule: _ChannelRule,
) -> str:
    """Apply the email routing rules and record the log row.

    The actual email send for the weekly digest lives in
    :mod:`backend.services.notifications`. The dispatcher's job here
    is to gate the send (bounce, prefs) and own the log write.

    Returns:
        Outcome string the caller folds into :class:`DispatchResult`.
    """
    if user.email_bounced_at is not None:
        return "skipped:bounced"

    if rule.prefs_field is not None and not getattr(prefs, rule.prefs_field):
        return f"skipped:prefs:{rule.prefs_field}"

    claimed = log_repo.claim(
        session,
        user_id=user.id,
        notification_type=trigger.notification_type.value,
        dedupe_key=trigger.dedupe_key,
        channel=Channel.EMAIL.value,
        payload=trigger.payload,
        now=trigger.trigger_time,
    )
    if not claimed:
        return "skipped:duplicate"

    # Email sends for digest/staff picks happen via existing pipelines
    # that own their own template + Resend call. Once those exist as
    # NotificationType-routable triggers, this is where we'd hand off.
    return "claimed"


# ---------------------------------------------------------------------------
# Push render
# ---------------------------------------------------------------------------


def _render_push(trigger: NotificationTrigger) -> push_service.PushPayload | None:
    """Build the push payload for a trigger.

    Each notification type has a tiny renderer here so the dispatcher
    decision tree stays in one place. Renderers tolerate missing
    payload keys defensively — a partially-populated trigger should
    surface as "skipped:no_renderer" instead of crashing the worker.

    Args:
        trigger: The trigger to render.

    Returns:
        A populated :class:`PushPayload`, or ``None`` when the
        trigger lacks the keys this renderer needs (the dispatcher
        skips with ``skipped:no_renderer`` on a None return).
    """
    payload = trigger.payload
    if trigger.notification_type is NotificationType.TOUR_ANNOUNCEMENT:
        artist = payload.get("performer_name")
        venue = payload.get("venue_name")
        date = payload.get("date_label")
        url = payload.get("url")
        if not (artist and venue and url):
            return None
        body_parts = [venue]
        if date:
            body_parts.append(str(date))
        return push_service.PushPayload(
            title=f"{artist} announced 🎫",
            body=" · ".join(str(p) for p in body_parts),
            url=str(url),
            tag=f"tour:{trigger.dedupe_key}",
        )
    if trigger.notification_type is NotificationType.VENUE_ANNOUNCEMENT:
        venue = payload.get("venue_name")
        artist = payload.get("performer_name")
        date = payload.get("date_label")
        url = payload.get("url")
        if not (venue and artist and url):
            return None
        return push_service.PushPayload(
            title=f"New at {venue}",
            body=f"{artist}{' · ' + str(date) if date else ''}",
            url=str(url),
            tag=f"venue:{trigger.dedupe_key}",
        )
    if trigger.notification_type is NotificationType.SHOW_REMINDER_24H:
        artist = payload.get("performer_name") or "Show"
        venue = payload.get("venue_name") or ""
        doors = payload.get("doors_label")
        url = payload.get("url")
        if not url:
            return None
        body = venue
        if doors:
            body = f"{venue} · Doors {doors}" if venue else f"Doors {doors}"
        return push_service.PushPayload(
            title=f"{artist} · Tomorrow",
            body=body,
            url=str(url),
            tag=f"reminder24:{trigger.dedupe_key}",
        )
    if trigger.notification_type is NotificationType.SELLING_FAST:
        artist = payload.get("performer_name") or "Show"
        url = payload.get("url")
        if not url:
            return None
        return push_service.PushPayload(
            title=f"{artist} is going fast",
            body="Saved show — tickets selling quickly",
            url=str(url),
            tag=f"sellingfast:{trigger.dedupe_key}",
        )
    return None


# ---------------------------------------------------------------------------
# Quiet-hour math
# ---------------------------------------------------------------------------


def _is_in_quiet_hours(prefs: NotificationPreferences, now: datetime) -> bool:
    """Re-export of the quiet-hours predicate, in this module's units.

    Kept here (rather than reusing the predicate in
    :mod:`notifications`) so the dispatcher does not need to import
    the legacy weekly-digest module just to see the time math.
    """
    local_hour = now.astimezone(ZoneInfo(prefs.timezone)).hour
    start = prefs.quiet_hours_start
    end = prefs.quiet_hours_end
    if start == end:
        return False
    if start < end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


def _next_non_quiet_hour(prefs: NotificationPreferences, now: datetime) -> datetime:
    """Return the next ``end``-hour boundary in UTC.

    Used as the ``eta`` for Celery deferrals when a trigger lands
    inside the user's quiet window. Always returns a future timestamp.
    """
    tz = ZoneInfo(prefs.timezone)
    local = now.astimezone(tz)
    target_hour = prefs.quiet_hours_end
    candidate = local.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)
