"""SQLAlchemy ORM models for email tracking and notification preferences.

Two related tables live here:

* :class:`EmailDigestLog` — append-only history of every email sent,
  with delivery + open + click data fed back from Resend webhooks.
* :class:`NotificationPreferences` — per-user toggles and frequency
  caps consulted before any email is sent. Granular by design so a
  user can opt into "selling fast" alerts without opting into the
  weekly digest.

The two models are kept in one module because every email path needs
both — the preference check, then the log write — and importing them
in a single shot keeps the call sites clean.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, TimestampMixin


class EmailDigestLog(TimestampMixin, Base):
    """A log of an email digest sent to a user.

    Tracks delivery, opens, and clicks via Resend webhook data.

    Attributes:
        id: Unique identifier for the digest log entry.
        user_id: Foreign key to the recipient user.
        digest_type: Type of digest (e.g., "weekly", "daily").
        event_count: Number of events included in the digest.
        sent_at: When the email was sent.
        provider_message_id: Resend message ID for tracking.
        opened_at: When the email was first opened, if tracked.
        clicked_at: When the email was first clicked, if tracked.
        metadata_json: Additional digest metadata as JSONB.
    """

    __tablename__ = "email_digest_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    digest_type: Mapped[str] = mapped_column(String(20), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clicked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Return a string representation of the EmailDigestLog.

        Returns:
            String representation with user ID and digest type.
        """
        return f"<EmailDigestLog user={self.user_id} type={self.digest_type}>"


class DigestDayOfWeek(enum.StrEnum):
    """Day-of-week labels accepted by ``digest_day_of_week``.

    Stored as the lower-case English weekday name so the value is
    self-describing in the database. Translation to a numeric weekday
    happens at the Celery scheduler boundary; this enum is the shape
    the API and database agree on.
    """

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class NotificationPreferences(TimestampMixin, Base):
    """Per-user notification preferences for every email type.

    One row per user (uniqueness enforced by the schema). The migration
    that creates this table also backfills a row for every existing
    user with the column defaults so reads never see a missing record.
    A new user's row is created lazily by the repository's
    ``get_or_create_for_user`` on first access — this guards against
    the rare race where a user is created between migrations and the
    first preference fetch.

    The "Pause all" affordance on the settings page does not delete
    preferences. Toggling it on snapshots the current per-type flags
    into ``paused_snapshot`` and stamps ``paused_at``; toggling it
    back restores the snapshot. While paused, the service layer
    treats every per-type flag as False without rewriting the row.

    Attributes:
        id: Primary key (UUID).
        user_id: Foreign key to the user. Unique — one row per user.
        artist_announcements: Whether to email when a followed
            artist's show is added to the calendar.
        venue_announcements: Whether to email when a followed venue
            adds a new show.
        selling_fast_alerts: Whether to email when a saved/going show
            crosses the "selling fast" availability threshold.
        show_reminders: Whether to email a reminder before a saved
            show.
        show_reminder_days_before: How many days before the show to
            remind. Constrained to 1, 2, or 7.
        staff_picks: Whether to receive staff-pick discovery emails.
        artist_spotlights: Whether to receive artist-spotlight emails.
        similar_artist_suggestions: Whether to receive similar-artist
            discovery emails.
        weekly_digest: Whether to receive the weekly digest.
        digest_day_of_week: Day to send the digest. Constrained to
            the seven English weekday names.
        digest_hour: Hour-of-day to send the digest in the user's
            timezone. Constrained to 0..23.
        max_emails_per_week: Cap on total emails per rolling week.
            Constrained to 1, 3, 7, or NULL (unlimited).
        quiet_hours_start: Hour-of-day quiet hours begin. 0..23.
        quiet_hours_end: Hour-of-day quiet hours end. 0..23. The two
            may straddle midnight (e.g., 21..8).
        timezone: IANA timezone string used to interpret quiet hours
            and the digest send time.
        paused_at: If set, the user has globally paused all email.
            Per-type flags are ignored while this is non-null.
        paused_snapshot: JSON snapshot of the per-type flags as they
            were just before the pause; restored when the pause is
            lifted.
    """

    __tablename__ = "notification_preferences"

    __table_args__ = (
        CheckConstraint(
            "digest_day_of_week IN ("
            "'monday','tuesday','wednesday','thursday',"
            "'friday','saturday','sunday')",
            name="ck_notif_pref_digest_day_of_week",
        ),
        CheckConstraint(
            "digest_hour >= 0 AND digest_hour <= 23",
            name="ck_notif_pref_digest_hour_range",
        ),
        CheckConstraint(
            "quiet_hours_start >= 0 AND quiet_hours_start <= 23",
            name="ck_notif_pref_quiet_start_range",
        ),
        CheckConstraint(
            "quiet_hours_end >= 0 AND quiet_hours_end <= 23",
            name="ck_notif_pref_quiet_end_range",
        ),
        CheckConstraint(
            "show_reminder_days_before IN (1, 2, 7)",
            name="ck_notif_pref_reminder_days",
        ),
        CheckConstraint(
            "max_emails_per_week IS NULL OR max_emails_per_week IN (1, 3, 7)",
            name="ck_notif_pref_max_per_week",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    artist_announcements: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    venue_announcements: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    selling_fast_alerts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    show_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_reminder_days_before: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )

    staff_picks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    artist_spotlights: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    similar_artist_suggestions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    weekly_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest_day_of_week: Mapped[str] = mapped_column(
        String(10), nullable=False, default=DigestDayOfWeek.MONDAY.value
    )
    digest_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=8)

    max_emails_per_week: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=3
    )
    quiet_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=21)
    quiet_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/New_York"
    )

    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Return a string representation of the NotificationPreferences row.

        Returns:
            String representation with the user ID and pause state.
        """
        paused = "paused" if self.paused_at is not None else "active"
        return f"<NotificationPreferences user={self.user_id} {paused}>"
