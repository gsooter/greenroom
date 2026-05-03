"""add_push_subscriptions_table

Revision ID: 9aa2bb3cc4dd
Revises: 9aa1bb2cc3dd
Create Date: 2026-05-03 21:30:00.000000

Creates ``push_subscriptions``, the per-device record produced when
a user grants browser notification permission inside the installed
PWA. One row per (user, endpoint) — a user with two browsers gets
two rows, but reinstalling on the same browser overwrites the
existing row via the ON CONFLICT DO UPDATE the subscribe endpoint
issues.

Includes the failure-tracking columns the push dispatcher uses to
auto-disable broken endpoints. ``disabled_at`` is nullable; reads
filter on ``IS NULL`` to skip dead rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9aa2bb3cc4dd"
down_revision: str | None = "9aa1bb2cc3dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``push_subscriptions`` table and supporting indexes."""
    op.create_table(
        "push_subscriptions",
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
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh_key", sa.String(length=200), nullable=False),
        sa.Column("auth_key", sa.String(length=60), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "last_successful_send_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "endpoint", name="uq_push_sub_user_endpoint"
        ),
    )
    op.create_index(
        "idx_push_sub_user", "push_subscriptions", ["user_id"]
    )


def downgrade() -> None:
    """Drop the ``push_subscriptions`` table."""
    op.drop_index("idx_push_sub_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
