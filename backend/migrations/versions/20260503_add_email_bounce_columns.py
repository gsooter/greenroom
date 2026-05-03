"""add_email_bounce_columns_to_users

Revision ID: 9aa1bb2cc3dd
Revises: b8c9d0e1f2a3
Create Date: 2026-05-03 21:00:00.000000

Adds ``users.email_bounced_at`` and ``users.email_bounce_reason`` so
the Resend webhook handler can mark addresses that have hard-bounced
or generated a complaint. The send pipeline checks
``email_bounced_at`` before composing transactional or digest mail —
a non-null value short-circuits the send so we don't keep paying
Resend for guaranteed-fail attempts that also harm sender reputation.

Both columns are nullable; an unbounced user has both as NULL. The
admin "clear bounce" affordance sets both back to NULL after the
recipient updates their address in Knuckles.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9aa1bb2cc3dd"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two bounce-tracking columns to ``users``."""
    op.add_column(
        "users",
        sa.Column(
            "email_bounced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_bounce_reason",
            sa.String(length=200),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the bounce-tracking columns."""
    op.drop_column("users", "email_bounce_reason")
    op.drop_column("users", "email_bounced_at")
