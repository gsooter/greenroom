"""Email digest assembly and send orchestration.

This module owns three responsibilities:

1. **Schedule guards** (:func:`is_in_quiet_hours`,
   :func:`is_due_for_weekly_digest`, :func:`is_at_weekly_cap`) — pure
   predicates the dispatcher consults before fanning out a send.
   Each guard interprets times in the user's IANA timezone so a user
   in Pacific time gets their digest at 08:00 PST, not 08:00 UTC.
2. **Content assembly** — building the per-user list of show cards
   that becomes the body of the email. (Added in Phase 3.3.)
3. **Send pipeline** — composing, sending, and logging an outbound
   digest. (Added in Phase 3.4.)

The hourly :func:`dispatch_weekly_digests` Celery task ties them
together: it iterates users with ``weekly_digest=True`` whose local
time matches their configured ``digest_day_of_week`` /
``digest_hour``, applies the cap and quiet-hours guards, and enqueues
a per-user send job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.data.repositories import email_digest_log as digest_log_repo
from backend.data.repositories import events as events_repo
from backend.data.repositories import notification_log as log_repo
from backend.data.repositories import notification_preferences as prefs_repo
from backend.data.repositories import users as users_repo
from backend.recommendations import engine as rec_engine
from backend.services import email as email_service
from backend.services import email_structured_data

logger = get_logger(__name__)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.notifications import NotificationPreferences
    from backend.data.models.users import User


# How many show cards a single weekly digest features. Set so the
# email is scannable on mobile (one screenful) and so a quiet week
# of upcoming shows still produces a respectable body.
_MAX_DIGEST_SHOWS: int = 6

# Idempotency window for weekly digests. A user must wait at least
# this many days since their last weekly send before another goes
# out, so a re-run of the dispatcher (or a clock-skewed beat) inside
# the same hour can't fire two emails at the same recipient.
_WEEKLY_IDEMPOTENCY_DAYS: int = 6

# String stored in ``email_digest_log.digest_type`` for the weekly
# digest. Other digest types will land alongside this constant.
_WEEKLY_DIGEST_TYPE: str = "weekly"


def is_in_quiet_hours(
    prefs: NotificationPreferences,
    now: datetime,
) -> bool:
    """Return True if ``now`` falls inside the user's quiet window.

    Quiet hours are stored as start/end ints in 0..23 in the user's
    own timezone. The window may straddle midnight (e.g., 21..8 means
    21:00 through 07:59). The end hour itself is the wake-up hour and
    is treated as outside the window — a user with quiet hours 21..8
    gets emails again at exactly 08:00.

    Args:
        prefs: The user's notification preferences row.
        now: Current wall-clock time. Must be timezone-aware; the
            function converts to the user's tz before comparing.

    Returns:
        True if the user's local hour is inside the quiet window.
    """
    local_hour = _localize(now, prefs.timezone).hour
    start = prefs.quiet_hours_start
    end = prefs.quiet_hours_end
    if start == end:
        return False
    if start < end:
        return start <= local_hour < end
    # Wrap-around window (e.g., 21..8): in if hour >= start OR hour < end.
    return local_hour >= start or local_hour < end


def is_due_for_weekly_digest(
    prefs: NotificationPreferences,
    now: datetime,
) -> bool:
    """Return True if a weekly digest should be sent to this user now.

    Four conditions must hold:

    * ``weekly_digest`` is enabled on the row.
    * The user is not globally paused.
    * Today's weekday in the user's tz matches
      ``digest_day_of_week``.
    * The current hour in the user's tz matches ``digest_hour``.

    Quiet hours are not consulted here — the dispatcher's quiet-hours
    guard is a separate predicate so a user can intentionally schedule
    their digest inside their quiet window if they want to.

    Args:
        prefs: The user's notification preferences row.
        now: Current wall-clock time. Must be timezone-aware.

    Returns:
        True if the row is due for a weekly digest in the current
        hour.
    """
    if not prefs.weekly_digest or prefs.paused_at is not None:
        return False
    local = _localize(now, prefs.timezone)
    weekday_name = local.strftime("%A").lower()
    return weekday_name == prefs.digest_day_of_week and local.hour == prefs.digest_hour


def is_at_weekly_cap(
    session: Session,
    user_id: uuid.UUID,
    prefs: NotificationPreferences,
    now: datetime,
) -> bool:
    """Return True if the user has hit ``max_emails_per_week``.

    The cap counts every digest log row in the trailing 7 days; once
    the count meets or exceeds the configured ceiling, the user is at
    cap and the dispatcher must skip them. ``max_emails_per_week=None``
    means unlimited.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        prefs: The user's notification preferences row. Read for
            ``max_emails_per_week``.
        now: Current wall-clock time. The trailing-7-day window is
            anchored to this value; tests pass a fixed clock.

    Returns:
        True when the user has already received their quota.
    """
    cap = prefs.max_emails_per_week
    if cap is None:
        return False
    since = now - timedelta(days=7)
    count = digest_log_repo.count_recent_for_user(session, user_id, since)
    return count >= cap


def _localize(now: datetime, tz_name: str) -> datetime:
    """Convert a timezone-aware datetime to the user's IANA tz.

    Args:
        now: A timezone-aware datetime (tests pass UTC, prod passes
            ``datetime.now(UTC)``).
        tz_name: IANA timezone string from the user's preferences.

    Returns:
        ``now`` projected into the named timezone.
    """
    return now.astimezone(ZoneInfo(tz_name))


# ---------------------------------------------------------------------------
# Content assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyDigest:
    """Assembled body for a single weekly-digest email.

    The dataclass exists so the dispatcher can reason about "did the
    assembler return anything worth sending?" before calling the
    renderer. :meth:`template_context` produces the dict the email
    template expects.

    Attributes:
        heading: Top-of-email headline ("Your week in DC").
        intro: One-line lede shown above the show list.
        preheader: Pre-header text shown in the inbox preview.
        cta_url: "View all shows" link at the bottom of the email.
        shows: Pre-flattened show-card dicts ready for Jinja.
        structured_data: List of Schema.org ``MusicEvent`` blobs
            embedded in the rendered HTML's ``<head>`` so Gmail and
            Apple Mail render rich actionable previews. Empty list
            when the digest has no shows.
    """

    heading: str
    intro: str
    preheader: str
    cta_url: str
    shows: list[dict[str, Any]]
    structured_data: list[dict[str, Any]]

    def template_context(self) -> dict[str, Any]:
        """Return the dict the email template will render against.

        Returns:
            A flat mapping of template-variable names to values.
        """
        return {
            "heading": self.heading,
            "intro": self.intro,
            "preheader": self.preheader,
            "cta_url": self.cta_url,
            "shows": self.shows,
            "structured_data": self.structured_data,
        }


def assemble_weekly_digest(
    session: Session,
    user: User,
    prefs: NotificationPreferences,
    *,
    now: datetime | None = None,
) -> WeeklyDigest | None:
    """Build the per-user content for a weekly digest email.

    Calls the recommendation engine to refresh the user's persisted
    recs, then intersects the top scored events with the next 7 days
    of upcoming shows in the user's city. Falls back to chronological
    order when the user has no recommendation rows (cold-start: no
    follows, no music cache, no genre picks).

    Args:
        session: Active SQLAlchemy session.
        user: The recipient :class:`User` row.
        prefs: The user's notification preferences (read for tz when
            building the friendly date label on each show card).
        now: Override the wall clock; defaults to ``datetime.now(UTC)``.
            Tests pass a fixed value to make ranking deterministic.

    Returns:
        A populated :class:`WeeklyDigest`, or ``None`` when the city
        has zero upcoming shows in the window.
    """
    now = now or datetime.now(UTC)
    events, _total = events_repo.list_events(
        session,
        city_id=user.city_id,
        date_from=now.date(),
        date_to=(now + timedelta(days=7)).date(),
        available_only=True,
        per_page=100,
    )
    if not events:
        return None

    rec_engine.generate_for_user(session, user)
    rec_rows, _ = users_repo.list_recommendations(
        session, user.id, page=1, per_page=200
    )
    score_by_event_id = {rec.event_id: rec.score for rec in rec_rows}

    ranked = sorted(
        events,
        key=lambda e: (
            -score_by_event_id.get(e.id, 0.0),
            e.starts_at,
        ),
    )
    top = ranked[:_MAX_DIGEST_SHOWS]
    cards = [_show_card(event, prefs) for event in top]
    structured = [
        email_structured_data.event_to_jsonld(
            event, event_url=f"{_public_base()}/events/{event.slug}"
        )
        for event in top
    ]

    show_word = "show" if len(cards) == 1 else "shows"
    has_recs = any(score_by_event_id.get(e.id, 0.0) > 0 for e in top)
    intro = (
        f"Here are the shows we think are worth your time, {_first_name(user)}."
        if has_recs
        else (
            f"Here's what's coming up this week, {_first_name(user)}. "
            "Connect Spotify or follow a few artists to get personalized picks."
        )
    )
    return WeeklyDigest(
        heading="Your week ahead",
        intro=intro,
        preheader=f"{len(cards)} upcoming {show_word} we picked for you",
        cta_url=f"{_public_base()}/events",
        shows=cards,
        structured_data=structured,
    )


def _show_card(event: Any, prefs: NotificationPreferences) -> dict[str, Any]:
    """Flatten an :class:`Event` into the dict the template renders.

    Args:
        event: The event row to flatten.
        prefs: The recipient's preferences. Read for ``timezone`` so
            the date label matches what the user would see in the app.

    Returns:
        A small dict of pre-formatted strings the template renders
        verbatim. Keeping the formatting here (rather than in Jinja)
        means the plain-text email reuses the same labels as the HTML.
    """
    starts = event.starts_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)
    local = starts.astimezone(ZoneInfo(prefs.timezone))
    date_label = local.strftime("%A, %b %-d · %-I:%M %p").lstrip()
    date_short = local.strftime("%a %b %-d").upper()
    time_short = local.strftime("%-I:%M %p")
    return {
        "headliner": event.title,
        "venue": event.venue.name,
        "date_label": date_label,
        "date_short": date_short,
        "time_short": time_short,
        "image_url": event.image_url,
        "url": f"{_public_base()}/events/{event.slug}",
    }


def _public_base() -> str:
    """Return the public site base URL with no trailing slash.

    Returns:
        The configured ``frontend_base_url`` with any trailing
        slash stripped. Used to compose share-grade event URLs in
        the digest body.
    """
    return get_settings().frontend_base_url.rstrip("/")


def _first_name(user: User) -> str:
    """Return a friendly first name for the digest greeting.

    Args:
        user: The recipient :class:`User`.

    Returns:
        The first whitespace-separated token of ``display_name``,
        or ``"there"`` when ``display_name`` is missing — so the
        intro reads "…worth your time, there." rather than failing
        loudly.
    """
    name = (user.display_name or "").strip()
    if not name:
        return "there"
    return name.split()[0]


# ---------------------------------------------------------------------------
# Send pipeline
# ---------------------------------------------------------------------------


def send_weekly_digest_to_user(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Send the weekly digest to a single user, if every guard passes.

    Re-checks the cap and idempotency guards inside the per-user
    task on purpose: the dispatcher's view of the world can be
    minutes stale, and a duplicate beat run must not produce a
    duplicate email. The function returns a boolean rather than
    raising on "skipped" cases so the dispatcher can rack up
    skip-counters in its log line for fleet observability.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the recipient.
        now: Override the wall clock; defaults to ``datetime.now(UTC)``.

    Returns:
        ``True`` when an email was actually sent and a log row
        written. ``False`` when the call was a no-op (user missing,
        paused, capped, already-sent, or no content for the week).

    Raises:
        AppError: ``EMAIL_DELIVERY_FAILED`` propagates out of
            ``compose_email`` when Resend rejects the message. The
            caller (the Celery task wrapper) decides whether to retry.
    """
    now = now or datetime.now(UTC)

    user = users_repo.get_user_by_id(session, user_id)
    if user is None:
        return False

    if user.email_bounced_at is not None:
        # The recipient's address bounced or generated a complaint.
        # The Resend webhook handler set the flag; admin clears it
        # after the user updates their address. Skip silently — the
        # user already knows something is wrong with their inbox.
        return False

    prefs = prefs_repo.get_or_create_for_user(session, user_id)
    if prefs.paused_at is not None or not prefs.weekly_digest:
        return False

    if _has_recent_weekly_log(session, user_id, now):
        return False

    if is_at_weekly_cap(session, user_id, prefs, now):
        return False

    digest = assemble_weekly_digest(session, user, prefs, now=now)
    if digest is None:
        return False

    # Claim a notification_log slot before sending. This makes the
    # weekly digest visible to the unified dispatcher's "what did we
    # send?" view alongside push notifications, and the unique
    # constraint provides a second line of defense against a duplicate
    # send within the same ISO week.
    iso_year, iso_week, _ = now.isocalendar()
    dedupe_key = f"{iso_year}-W{iso_week:02d}"
    claimed = log_repo.claim(
        session,
        user_id=user_id,
        notification_type="weekly_digest",
        dedupe_key=dedupe_key,
        channel="email",
        payload={"show_count": len(digest.shows)},
        now=now,
    )
    if not claimed:
        return False

    email_service.compose_email(
        to=user.email,
        user_id=user_id,
        subject=digest.heading,
        template="show_announcement",
        scope="weekly_digest",
        context=digest.template_context(),
    )

    digest_log_repo.create_log(
        session,
        user_id=user_id,
        digest_type=_WEEKLY_DIGEST_TYPE,
        event_count=len(digest.shows),
        sent_at=now,
        metadata_json={
            "show_count": len(digest.shows),
        },
    )
    return True


def _has_recent_weekly_log(
    session: Session,
    user_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Return True if the user already received a weekly digest recently.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the recipient.
        now: Current wall-clock time.

    Returns:
        True if the most-recent weekly log row's ``sent_at`` falls
        inside the trailing :data:`_WEEKLY_IDEMPOTENCY_DAYS` window.
    """
    last = digest_log_repo.get_most_recent_for_type(
        session, user_id, _WEEKLY_DIGEST_TYPE
    )
    if last is None:
        return False
    sent_at = last.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return now - sent_at < timedelta(days=_WEEKLY_IDEMPOTENCY_DAYS)


# ---------------------------------------------------------------------------
# Hourly dispatcher
# ---------------------------------------------------------------------------


SendFn = Callable[..., bool]
"""Signature for the per-user send callable.

Defined as a type alias so :func:`dispatch_weekly_digests` can accept
a stub from tests while the production caller passes
:func:`send_weekly_digest_to_user`. The callable takes
``(session, user_id, *, now)`` and returns ``True`` when an email
was actually sent.
"""


def dispatch_weekly_digests(
    session: Session,
    *,
    now: datetime | None = None,
    send_fn: SendFn | None = None,
) -> dict[str, int]:
    """Fan out the weekly digest to every user due in the current hour.

    Pulls the candidate set with
    :func:`prefs_repo.list_active_weekly_digest_subscribers` (which
    already filters for ``weekly_digest=True`` and ``paused_at IS NULL``)
    and walks it once. For each row the dispatcher applies two pure
    predicates — :func:`is_due_for_weekly_digest` and
    :func:`is_in_quiet_hours` — before invoking ``send_fn`` for the
    per-user pipeline.

    A ``send_fn`` exception is logged and counted as an error rather
    than re-raised so one bad row cannot stall the rest of the run.
    Cap and idempotency guards live inside ``send_fn`` itself, so a
    ``False`` return there is treated as a skip.

    Args:
        session: Active SQLAlchemy session.
        now: Override the wall clock; defaults to ``datetime.now(UTC)``.
            Tests pass a fixed value so weekday/hour math is
            deterministic.
        send_fn: Per-user send callable. Defaults to
            :func:`send_weekly_digest_to_user`. Tests pass a stub.

    Returns:
        A summary dict the Celery wrapper logs as a single structured
        line: ``candidates`` (rows scanned), ``sent`` (emails actually
        delivered), ``skipped_not_due`` (wrong weekday/hour),
        ``skipped_quiet_hours`` (digest hour falls inside the user's
        quiet window), ``skipped_send_returned_false`` (send_fn
        no-opped — cap, idempotency, paused, or no content), and
        ``errors`` (send_fn raised).
    """
    now = now or datetime.now(UTC)
    fn: SendFn = send_fn or send_weekly_digest_to_user

    summary: dict[str, int] = {
        "candidates": 0,
        "sent": 0,
        "skipped_not_due": 0,
        "skipped_quiet_hours": 0,
        "skipped_send_returned_false": 0,
        "errors": 0,
    }

    candidates = prefs_repo.list_active_weekly_digest_subscribers(session)
    summary["candidates"] = len(candidates)

    for prefs in candidates:
        if not is_due_for_weekly_digest(prefs, now):
            summary["skipped_not_due"] += 1
            continue
        if is_in_quiet_hours(prefs, now):
            summary["skipped_quiet_hours"] += 1
            continue
        try:
            sent = fn(session, prefs.user_id, now=now)
        except Exception:
            summary["errors"] += 1
            logger.exception(
                "weekly_digest_send_failed",
                extra={"user_id": str(prefs.user_id)},
            )
            continue
        if sent:
            summary["sent"] += 1
        else:
            summary["skipped_send_returned_false"] += 1

    return summary
