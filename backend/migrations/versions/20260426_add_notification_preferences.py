"""add_notification_preferences

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-26 18:00:00.000000

Introduces the granular email-notification preference system that backs
the new ``/settings/notifications`` page (Phase 1 of the email sprint).

Why a dedicated table rather than expanding the JSONB blob on
``users.notification_settings``:

* The settings UI exposes 15+ toggles plus three numeric ranges; each
  triggered-email path checks a specific column hundreds of times an
  hour. A dedicated table gives us indexed reads, real defaults, and
  CHECK constraints rather than per-read Python-side coercion.
* Future per-type frequency caps and per-type cooldown timestamps can
  be added as columns without rewriting JSONB blobs.

Backfill: every existing user gets a row with the defaults defined in
this migration so the very next ``GET /me/notification-preferences``
returns a valid record without an upsert path. The legacy
``users.notification_settings`` column is left in place for now and is
removed in a follow-up migration after the new flow is live.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VALID_DAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def upgrade() -> None:
    """Create ``notification_preferences`` and backfill rows for users."""
    op.create_table(
        "notification_preferences",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Show alerts — default on (these are directly useful).
        sa.Column(
            "artist_announcements",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "venue_announcements",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "selling_fast_alerts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "show_reminders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "show_reminder_days_before",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        # Discovery — default off (opt in to avoid overwhelming new users).
        sa.Column(
            "staff_picks",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "artist_spotlights",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "similar_artist_suggestions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Digest — default off; user opts in.
        sa.Column(
            "weekly_digest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "digest_day_of_week",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'monday'"),
        ),
        sa.Column(
            "digest_hour",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
        # Global controls.
        sa.Column(
            "max_emails_per_week",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("3"),
        ),
        sa.Column(
            "quiet_hours_start",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("21"),
        ),
        sa.Column(
            "quiet_hours_end",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'America/New_York'"),
        ),
        # "Pause all" pre-toggle snapshot — when ``paused_at`` is set, the
        # service layer flips every notification flag to False on read.
        # Restoring is a copy-back from this snapshot. Stored as JSONB
        # rather than per-column shadow so the schema stays flat.
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "paused_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"digest_day_of_week IN ({', '.join(repr(d) for d in _VALID_DAYS)})",
            name="ck_notif_pref_digest_day_of_week",
        ),
        sa.CheckConstraint(
            "digest_hour >= 0 AND digest_hour <= 23",
            name="ck_notif_pref_digest_hour_range",
        ),
        sa.CheckConstraint(
            "quiet_hours_start >= 0 AND quiet_hours_start <= 23",
            name="ck_notif_pref_quiet_start_range",
        ),
        sa.CheckConstraint(
            "quiet_hours_end >= 0 AND quiet_hours_end <= 23",
            name="ck_notif_pref_quiet_end_range",
        ),
        sa.CheckConstraint(
            "show_reminder_days_before IN (1, 2, 7)",
            name="ck_notif_pref_reminder_days",
        ),
        sa.CheckConstraint(
            "max_emails_per_week IS NULL OR max_emails_per_week IN (1, 3, 7)",
            name="ck_notif_pref_max_per_week",
        ),
    )
    op.create_index(
        "ix_notification_preferences_user_id",
        "notification_preferences",
        ["user_id"],
    )

    # Backfill: one preference row per existing user with the column
    # defaults above. This avoids an upsert path on first read.
    op.execute(
        sa.text(
            """
            INSERT INTO notification_preferences (user_id)
            SELECT id FROM users
            WHERE NOT EXISTS (
                SELECT 1 FROM notification_preferences np
                WHERE np.user_id = users.id
            )
            """
        )
    )


def downgrade() -> None:
    """Drop the notification_preferences table and its index."""
    op.drop_index(
        "ix_notification_preferences_user_id",
        table_name="notification_preferences",
    )
    op.drop_table("notification_preferences")
