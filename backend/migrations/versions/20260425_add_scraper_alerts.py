"""add_scraper_alerts

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-04-25 09:00:00.000000

Introduces the ``scraper_alerts`` table that the notifier consults
before posting to Slack/email. Each row tracks the last delivery time
for a given ``alert_key`` so repeats inside the operator-defined
cooldown window are silently suppressed. Without this table, a single
broken venue would post on every nightly run and on every manual
``/admin`` re-trigger — drowning the channel and training the operator
to ignore real signal.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``scraper_alerts`` table and its supporting indexes."""
    op.create_table(
        "scraper_alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("alert_key", sa.String(length=200), nullable=False),
        sa.Column(
            "last_sent_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "sent_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("alert_key", name="uq_scraper_alerts_alert_key"),
    )
    op.create_index(
        "ix_scraper_alerts_alert_key",
        "scraper_alerts",
        ["alert_key"],
    )
    op.create_index(
        "ix_scraper_alerts_last_sent_at",
        "scraper_alerts",
        ["last_sent_at"],
    )


def downgrade() -> None:
    """Drop the ``scraper_alerts`` table and its indexes."""
    op.drop_index(
        "ix_scraper_alerts_last_sent_at", table_name="scraper_alerts"
    )
    op.drop_index("ix_scraper_alerts_alert_key", table_name="scraper_alerts")
    op.drop_table("scraper_alerts")
