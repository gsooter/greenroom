"""add_notification_log_table

Revision ID: 9aa3bb4cc5dd
Revises: 9aa2bb3cc4dd
Create Date: 2026-05-03 22:00:00.000000

Creates ``notification_log``, the append-only journal the unified
dispatcher writes to before fanning out a send. The unique constraint
on ``(user_id, notification_type, dedupe_key, channel)`` is the lock
that stops a duplicate scraper run, retried Celery task, or replayed
trigger from producing a duplicate notification.

The table is intentionally separate from ``email_digest_log`` rather
than a superset — the digest log carries channel-specific telemetry
(open/click/bounce) the dispatcher does not own, and renaming the
existing table would have rippled through ops dashboards.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9aa3bb4cc5dd"
down_revision: str | None = "9aa2bb3cc4dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``notification_log`` table with its dedupe constraint."""
    op.create_table(
        "notification_log",
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
        ),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("dedupe_key", sa.String(length=80), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "notification_type",
            "dedupe_key",
            "channel",
            name="uq_notification_log_dedupe",
        ),
    )
    op.create_index(
        "idx_notification_log_user", "notification_log", ["user_id"]
    )
    op.create_index(
        "idx_notification_log_user_sent_at",
        "notification_log",
        ["user_id", "sent_at"],
    )


def downgrade() -> None:
    """Drop the ``notification_log`` table."""
    op.drop_index(
        "idx_notification_log_user_sent_at", table_name="notification_log"
    )
    op.drop_index("idx_notification_log_user", table_name="notification_log")
    op.drop_table("notification_log")
